import argparse
import datetime
import email
import email.header
import email.policy
import io
import logging
import math
import os
from collections import namedtuple
from difflib import SequenceMatcher
from email.message import EmailMessage
from imaplib import IMAP4_SSL
from typing import List, Tuple, Union

import dateutil.parser
import firefly_iii_client
import pdfkit
import yaml
from firefly_iii_client.api import attachments_api, transactions_api
from firefly_iii_client.model.attachment_store import AttachmentStore
from firefly_iii_client.model.transaction_split import TransactionSplit
from firefly_iii_client.model.transaction_type_filter import TransactionTypeFilter

with open('config.yml') as c:
    config = yaml.load(c, Loader=yaml.FullLoader)

IMAP_FILTER = [
    'SINCE "{}"',
    'BEFORE "{}"'
]

MIN_MATCH_THRESHOLD = 0.27

logger = logging.getLogger(__name__)
logging.basicConfig(**config.get('logging'))

MessageItem = namedtuple('MessageItem', ['uid', 'message'])


def _score_message_by_transaction(message: EmailMessage, transaction: TransactionSplit) -> float:
    subject_content, subject_enc = email.header.decode_header(message['Subject'])[0]
    if subject_enc is not None:
        subject_content = subject_content.decode(subject_enc)
    subject = subject_content.lower()
    correspondent = message['From'].lower()
    description = transaction['description'].lower()
    s1 = SequenceMatcher(None, subject, description)
    s2 = SequenceMatcher(None, correspondent, description)
    subject_score = sum(match.size for match in s1.get_matching_blocks() if match.size > 2) / 10
    correspondent_score = sum(match.size for match in s2.get_matching_blocks() if match.size > 2) / 10

    message_body_content = __get_message_body(message, decode=True, preference_list=('plain', 'html'))
    value_string = transaction['currency_symbol'] + str(round(float(transaction['amount']), 2))
    content_score = float(value_string in message_body_content)

    message_date = dateutil.parser.parse(message['Date']).date()
    transaction_date = transaction['date'].date()
    date_days_diff = abs((message_date - transaction_date).days)
    date_score = date_days_diff / -10

    return subject_score * 0.2 + correspondent_score * 0.5 + content_score * 0.2 + date_score * 0.5


def _match_messages_to_transactions(
        message_items: List[MessageItem],
        transactions: List[TransactionSplit],
        min_match_threshold: float = 0.0
) -> List[Tuple[MessageItem, TransactionSplit]]:
    matches = []
    message_max_scores = {}
    for transaction in transactions:
        scores = [_score_message_by_transaction(message, transaction) for (_, message) in message_items]
        if scores:
            max_score = max(scores)
            while max_score >= min_match_threshold:
                message_item = message_items[scores.index(max_score)]
                logging.debug(
                    'transaction ("%s") max_score = %f with message from %s',
                    transaction.description,
                    max_score,
                    message_item.message['From']
                )
                if max_score >= message_max_scores.get(message_item, 0):
                    matches.append((message_item, transaction))
                    message_max_scores[message_item] = max_score
                    max_score = -math.inf
                else:
                    scores.pop(scores.index(max_score))
                    max_score = max(scores) if scores else -math.inf
    return matches


def __get_message_body(
        message: EmailMessage,
        decode=True,
        preference_list=('html', 'plain')
) -> Union[str, None]:
    if message.is_multipart():
        mail_body_plain = next((part
                                for part in message.walk()
                                if part.get_content_type() == 'text/plain'), None)
        mail_body_html = next((part
                               for part in message.walk()
                               if part.get_content_type() == 'text/html'), None)
        mail_body_part = mail_body_html or mail_body_plain
    else:
        mail_body = message.get_body(preferencelist=preference_list)
        mail_body_part = mail_body

    mail_body_content = mail_body_part.get_payload(decode=decode)
    if mail_body_content is not None:
        charset = next((s for s in mail_body_part.get_charsets() if s), None)
        return mail_body_content.decode(charset or 'utf-8') if decode else mail_body_content
    return None


def main():
    parser = argparse.ArgumentParser(prog='firl', description='Import emails from imap server into Firefly III.')
    parser.add_argument(
        '--date-from', '-df',
        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d').date(),
        help='Earliest date to import emails from in the format YYYY-mm-dd'
    )
    parser.add_argument(
        '--date-to', '-dt',
        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d').date(),
        help='Latest date to import emails from in the format YYYY-mm-dd'
    )
    parser.add_argument('--last', '-L', type=int, metavar='N', default=3, help='Import emails from the last N days')
    parser.add_argument('--dry-run', '-x', action='store_true')
    args = parser.parse_args()

    if args.dry_run:
        logger.warning('Performing dry-run, no attachments will be imported into Firefly!')

    if args.date_from is not None and args.date_to is not None:
        date_from = args.date_from
        date_to = args.date_to
    else:
        date_to = args.date_to if args.date_to is not None else datetime.date.today()
        date_from = date_to - datetime.timedelta(days=args.last)

    access_token = config.get('firefly').get('access_token')
    if os.path.exists(access_token):
        with open(access_token, 'r') as f:
            access_token = f.read()
    firefly_config = firefly_iii_client.Configuration(
        host=config.get('firefly').get('host'),
        access_token=access_token
    )

    if (date_to - date_from).days > 30:
        logging.warning('some mail providers may limit the number of messages '
                        'returned by this query, consider a smaller date range!')

    logging.info('connecting to imap @ %s', config.get('imap').get('host'))
    logging.info('connecting to firefly api @ %s', firefly_config.host)
    messages = []
    with IMAP4_SSL(
        host=config.get('imap').get('host'),
        port=config.get('imap').get('port')
    ) as imap, firefly_iii_client.ApiClient(
        firefly_config
    ) as api_client:
        imap.login(
            config.get('imap').get('user'),
            config.get('imap').get('password')
        )
        imap.select(mailbox=config.get('mailbox'), readonly=False)
        logging.info(
            'querying inbox for messages between %s and %s',
            date_from.strftime('%d-%b-%Y'),
            date_to.strftime('%d-%b-%Y')
        )
        imap_filter = [
            IMAP_FILTER[1].format(date_to.strftime('%d-%b-%Y')),
            IMAP_FILTER[0].format(date_from.strftime('%d-%b-%Y')),
        ]
        resp, data = imap.uid('SEARCH', *imap_filter)

        if resp == 'OK':
            for uid in map(int, data[0].split()):
                resp, data = imap.uid('fetch', str(uid), '(RFC822)')
                if resp == 'OK':
                    messages.append(MessageItem(
                        uid,
                        email.message_from_bytes(data[0][1], policy=email.policy.default)
                    ))
            logging.info('found %d messages in inbox', len(messages))

        transaction_instance = transactions_api.TransactionsApi(api_client)
        attachment_instance = attachments_api.AttachmentsApi(api_client)
        filter_type = TransactionTypeFilter("expense")
        attachments = transaction_instance.list_transaction(
            page=1, start=date_from, end=date_to, type=filter_type
        )
        transactions = [transaction['attributes']['transactions'][0] for transaction in attachments['data']]
        logging.info('found %d expense transactions to match', len(transactions))

        matches = _match_messages_to_transactions(messages, transactions, min_match_threshold=MIN_MATCH_THRESHOLD)
        logging.info('matched %d messages with transactions', len(matches))
        for (uid, message), transaction in matches:
            subject, subject_enc = email.header.decode_header(message['Subject'])[0]
            if subject_enc is not None:
                subject = subject.decode(subject_enc)
            logging.info(
                'matched message from %s - %s ("%s") to transaction ("%s")',
                message['From'],
                message['Date'],
                subject,
                transaction.description
            )

            logging.info('generating pdf of message')

            attachments = transaction_instance.list_attachment_by_transaction(int(transaction.transaction_journal_id))
            uploaded_file_names = [attachment['attributes']['filename'] for attachment in attachments['data']]

            # for attachment in attachments['data']:
            #     attachment_instance.delete_attachment(id=int(attachment['id']))
            try:
                message_body_content = __get_message_body(message, decode=True)
                if message_body_content is not None:
                    mail_body_pdf = pdfkit.from_string(
                        message_body_content,
                        options={
                            'no-images': True
                        } if message.is_multipart() else {}  # no support for images in multipart messages
                    )
                    logging.debug('pdf created size = %d', len(mail_body_pdf))
                    pdf_name = subject.replace(' ', '_') + '.pdf'

                    if pdf_name not in uploaded_file_names:
                        logging.info('uploading attachment "%s"', pdf_name)
                        if not args.dry_run:
                            attachment_store = AttachmentStore(
                                attachable_id=transaction.transaction_journal_id,
                                attachable_type='TransactionJournal',
                                filename=pdf_name,
                                title=subject
                            )
                            new_attachment = attachment_instance.store_attachment(attachment_store)

                            attachment_instance.upload_attachment(
                                id=int(new_attachment['data']['id']),
                                body=io.BytesIO(mail_body_pdf)
                            )
                else:
                    logging.warning('unable to find mail body so skipping PDF gen')

                for attachment in message.iter_attachments():
                    filename, file_extension = os.path.splitext(attachment.get_filename())
                    if file_extension in config.get('attachment_extensions') and filename not in uploaded_file_names:
                        logging.info('uploading attachment "%s"', attachment.get_filename())
                        if not args.dry_run:
                            attachment_store = AttachmentStore(
                                attachable_id=transaction.transaction_journal_id,
                                attachable_type='TransactionJournal',
                                filename=filename,
                                title=filename
                            )
                            new_attachment = attachment_instance.store_attachment(attachment_store)

                            attachment_instance.upload_attachment(
                                id=int(new_attachment['data']['id']),
                                body=io.BytesIO(attachment.get_payload(decode=True))
                            )

                if config.get('processed_mailbox'):
                    logging.info('copying message to "%s" mailbox', config.get('processed_mailbox'))
                    if not args.dry_run:
                        res, _ = imap.uid('COPY', str(uid), '"{}"'.format(config.get('processed_mailbox')))
                        if res == 'OK':
                            imap.uid('STORE', str(uid), '+FLAGS', '(\Deleted)')
                            imap.expunge()

            except OSError as e:
                logging.warning('unable to create PDF: %s', e)


if __name__ == '__main__':
    main()

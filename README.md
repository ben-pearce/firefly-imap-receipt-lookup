# 📧➡️🐷 firefly-imap-receipt-lookup

A tool for importing e-receipts into Firefly III finance manager from an IMAP mail server.

Emails are converted to PDF format and attached to transactions within Firefly.

Full-automation is achieved by matching transactions with email through the use of a simple scoring strategy, where the highest scoring match is chosen based on the similarity of various attributes (subject, content, sender etc).

## Usage

Easiest way is with `docker run`.

```shell
docker run --rm \
  --name=firl \
  -e IMAP_HOST=imap.example.com \
  -e IMAP_PORT=993 \
  -e IMAP_USER=username \
  -e IMAP_PASSWORD=password \
  -e FIREFLY_BASE_URL=https://firefly.example.com \
  -e MAILBOX=Receipts \
  -e PROCESSED_MAILBOX=Trash \
  -v ./firefly-token:/app/firefly-token \
  ghcr.io/ben-pearce/firefly-imap-receipt-lookup:latest \
  --last 5
```

### Environment Variables

| **Variable**            | **Function***                                                                                        |
|-------------------------|------------------------------------------------------------------------------------------------------|
| `IMAP_HOST`             | The host of the IMAP server to connect to.                                                           |
| `IMAP_PORT`             | The port of the IMAP server to connect to (default: `993`).                                          |
| `IMAP_USER`             | The user account of the IMAP server.                                                                 |
| `IMAP_PASSWORD`         | The password to the account of the IMAP server.                                                      |
| `LOGGING_LEVEL`         | The logging level to use for output (default: `INFO`).                                               |
| `FIREFLY_BASE_URL`      | Base URL of Firefly III for API access.                                                              |
| `FIREFLY_TOKEN`         | Path to file containing Firefly API token, or raw string (default: `./firefly-token`).               |
| `MAILBOX`               | The IMAP folder to retrieve mails from.                                                              |
| `PROCESSED_MAILBOX`     | The IMAP folder to dump processed mails in, or blank to disable (default: blank).                    |
| `ATTACHMENT_EXTENSIONS` | Mail attachment file types to also upload to Firefly (default: `['.pdf', '.jpeg', '.jpg', '.png']`). |

### Command-line Arguments

| **Argument**  | **Function***                                                    |
|---------------|------------------------------------------------------------------|
| `--date-to`   | Latest date to retrieve mails from in the format `YYYY-mm-dd`.   |
| `--date-from` | Earliest date to retrieve mails from in the format `YYYY-mm-dd`. |
| `--last`      | Retrieve mails in the last `N` days.                             |

**Note**: The `--date-to` and `--last` arguments are not mutually exclusive, using these together will result in a date range `last` `N` days prior to `date-to`. `--date-from` is ignored when the `--last` argument is provided.

## Cron Job

The best way to continuously import mail is via cron. Run once per-day with a date range of last 3 or 4 days.

```shell
# Write Firefly III token to file
echo "FIREFLY_TOKEN_HERE" > firefly-token

# Write a script which executes the importer
cat <<EOT >> firl.sh
docker run --rm \
  --name=firl \
  -e IMAP_HOST=imap.example.com \
  -e IMAP_PORT=993 \
  -e IMAP_USER=username \
  -e IMAP_PASSWORD=password \
  -e FIREFLY_BASE_URL=https://firefly.example.com \
  -e MAILBOX=Receipts \
  -e PROCESSED_MAILBOX=Trash \
  -v $(readlink -f firefly-token):/app/firefly-token \
  ghcr.io/ben-pearce/firefly-imap-receipt-lookup:latest \
  --last 5
EOT

# Make script executable
chmod +x firl.sh

# Add script to crontab to run DAILY
crontab -l | { cat; echo -n "0 0 * * * "; echo "$(readlink -f firl.sh)"; } | crontab -
```
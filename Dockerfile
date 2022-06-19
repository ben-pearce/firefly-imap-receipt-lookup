FROM python:3.9-alpine3.14
COPY . ./app
WORKDIR /app

RUN apk update && apk add --no-cache wkhtmltopdf xvfb ttf-dejavu ttf-droid ttf-freefont ttf-liberation patch
RUN ln -s /usr/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf;
RUN chmod +x /usr/local/bin/wkhtmltopdf;

ENV DOCKERIZE_VERSION v0.6.1
RUN wget https://github.com/jwilder/dockerize/releases/download/$DOCKERIZE_VERSION/dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz \
    && tar -C /usr/local/bin -xzvf dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz \
    && rm dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

RUN patch /usr/local/lib/python3.9/site-packages/firefly_iii_client/model/attachment.py patches/ff_attachments_api_none_bug.patch
RUN patch /usr/local/lib/python3.9/site-packages/firefly_iii_client/model_utils.py patches/ff_self_arg_missing.patch

ENTRYPOINT ["dockerize", "-template", "config.yml.tmpl:config.yml", "python3", "__init__.py"]
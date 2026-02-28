FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY app/requirments.txt /tmp/requirments.txt
RUN pip install --no-cache-dir -r /tmp/requirments.txt

COPY app /srv/app
RUN chmod +x /srv/app/entrypoint.sh

EXPOSE 8000

CMD ["bash", "app/entrypoint.sh"]

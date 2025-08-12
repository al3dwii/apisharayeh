FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /srv

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

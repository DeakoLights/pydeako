FROM python:3.11

WORKDIR /usr/src

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

COPY requirements_test.txt requirements_test.txt

RUN pip3 install -r requirements_test.txt
FROM python:3.12

SHELL ["/bin/bash", "-c"]

RUN apt update && apt install -y --no-install-recommends nginx python3-venv && \
    mkdir /dashy 

WORKDIR /dashy

COPY . .

RUN python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

CMD ["bash", "docker-start.sh"]
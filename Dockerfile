FROM python:3.12

SHELL ["/bin/bash", "-c"]

# Install Nginx, create dashy dir, create dashy user with a home folder of /dashy and set the default shell to bash
RUN apt update && apt install -y --no-install-recommends nginx python3-venv && \
    mkdir /dashy && useradd dashy -d /dashy -s /bin/bash

WORKDIR /dashy

COPY . .

RUN chown -R dashy:dashy /dashy && chmod 755 /dashy && \
    su dashy -c "python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"

CMD ["bash", "docker-start.sh"]
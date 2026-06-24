FROM python:3.12

WORKDIR /processor

RUN apt clean && apt-get update && apt-get -y install libhdf5-dev

COPY processor/requirements.txt /processor/requirements.txt

RUN pip install -r /processor/requirements.txt

COPY processor/ /processor

ENV PYTHONPATH="/"

##### v0.0.1 to v0.0.2: added this ###### 
# Pennsieve runs the container as a non-root user that cannot write to the
# default HOME (/). pynwb builds its cache path from ~/.cache at import time,
# so point HOME and the cache at /tmp (world-writable) to avoid
# PermissionError: [Errno 13] Permission denied: '/.cache'.
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache
##### v0.0.1 to v0.0.2: added this ###### 

CMD ["python3.12", "-m", "processor.main"]

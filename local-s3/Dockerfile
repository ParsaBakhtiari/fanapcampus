FROM python:3.12-slim
# Local S3-compatible server (moto) standing in for OBS in docker-compose only.
# Not used in CCE: there the services talk to the real OBS.
RUN pip install --no-cache-dir "moto[server]>=5.0,<6" "boto3>=1.34"
COPY server.py /server.py
ENV BUCKETS=ecommerce-images,ecommerce-invoices,ecommerce-backups
EXPOSE 9000
HEALTHCHECK --interval=5s --retries=20 CMD test -f /tmp/ready
CMD ["python", "/server.py"]

#!/bin/sh
# Runs once, on first initialisation of the data volume.
# Two logical databases with two roles. The learner application only ever receives the
# platform role; the content role is reserved for the back-office and the worker.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE ari_platform LOGIN PASSWORD '${ARI_PLATFORM_DB_PASSWORD}';
CREATE ROLE ari_content LOGIN PASSWORD '${ARI_CONTENT_DB_PASSWORD}';

CREATE DATABASE ari_platform OWNER ari_platform;
CREATE DATABASE ari_content OWNER ari_content;

REVOKE CONNECT ON DATABASE ari_platform FROM PUBLIC;
REVOKE CONNECT ON DATABASE ari_content FROM PUBLIC;
GRANT CONNECT ON DATABASE ari_platform TO ari_platform;
GRANT CONNECT ON DATABASE ari_content TO ari_content;
SQL

#!/bin/sh
# Local MySQL only (docker-compose). Mirrors scripts/rds-init.sql.
set -e
mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
CREATE DATABASE IF NOT EXISTS shop_db  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS order_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'shop'@'%'   IDENTIFIED BY '${SHOP_DB_PASSWORD}';
CREATE USER IF NOT EXISTS 'orders'@'%' IDENTIFIED BY '${ORDER_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON shop_db.*  TO 'shop'@'%';
GRANT ALL PRIVILEGES ON order_db.* TO 'orders'@'%';
FLUSH PRIVILEGES;
SQL

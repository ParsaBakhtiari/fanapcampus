-- Run once on the RDS MySQL instance (from the Bastion Host) as the admin user:
--   mysql -h <rds-private-ip> -u root -p < scripts/rds-init.sql
-- Replace both passwords first. Each microservice gets its own database and user.
--
-- Network access is limited by the RDS security group (RDS-SG: allow 3306 only
-- from CCE-SG, see the Phase 1 guide), so the user host is '%'. Pinning a host
-- here (e.g. '10.0.2.%') breaks as soon as pods get IPs from a container CIDR.

CREATE DATABASE IF NOT EXISTS shop_db  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS order_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'shop'@'%'   IDENTIFIED BY 'CHANGE_ME_shop_password';
CREATE USER IF NOT EXISTS 'orders'@'%' IDENTIFIED BY 'CHANGE_ME_orders_password';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES ON shop_db.*  TO 'shop'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES ON order_db.* TO 'orders'@'%';
FLUSH PRIVILEGES;

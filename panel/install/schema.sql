-- Radarban panel — MySQL / MariaDB schema (import once with phpMyAdmin)
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS users (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email         VARCHAR(190) NOT NULL UNIQUE,
  name          VARCHAR(100) NOT NULL DEFAULT '',
  pass_hash     VARCHAR(255) NOT NULL,
  is_admin      TINYINT(1)   NOT NULL DEFAULT 0,
  sub_until     DATETIME     NULL,            -- UTC; NULL = no active subscription
  created_at    DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS news (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  msg_id         VARCHAR(64)  NOT NULL UNIQUE,  -- m-939 / r-202611261800
  kind           VARCHAR(10)  NOT NULL DEFAULT 'card',  -- card | review
  code           INT          NULL,
  event_id       VARCHAR(64)  NULL,
  reply_to_event VARCHAR(64)  NULL,
  importance     INT          NULL,
  line           VARCHAR(64)  NULL,
  title          VARCHAR(500) NULL,
  sig_dir        VARCHAR(10)  NULL,
  sig_label      VARCHAR(64)  NULL,
  sig_symbol     VARCHAR(32)  NULL,
  sig_conf       VARCHAR(16)  NULL,
  html           MEDIUMTEXT   NOT NULL,
  created_at     DATETIME     NOT NULL,          -- UTC, when the analyst wrote it
  received_at    DATETIME     NOT NULL,
  KEY idx_event (event_id),
  KEY idx_kind_id (kind, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS login_attempts (
  ip  VARCHAR(45) NOT NULL,
  at  DATETIME    NOT NULL,
  KEY idx_ip_at (ip, at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

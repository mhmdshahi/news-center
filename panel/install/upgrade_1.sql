-- Only if you imported schema.sql BEFORE the delivery-queue update:
ALTER TABLE news ADD COLUMN rseq INT NULL AFTER msg_id;
CREATE TABLE IF NOT EXISTS meta (
  k  VARCHAR(64)  NOT NULL PRIMARY KEY,
  v  VARCHAR(500) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

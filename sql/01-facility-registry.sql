-- National facility registry.
-- Built from the master facility list in the facility schema (10 provinces, 69 districts,
-- 1,542 facilities). Only Avondale Clinic (ZW000A01) has clinical data in this database;
-- every other facility is registered but not yet reporting. In the future multi-tenant
-- database every clinical/report row will carry facility_id.
--
-- Facility type is not recorded in the master list, so it is derived, and the source of each
-- classification is stored in facility_type_source:
--   'known list' - Zimbabwe's central and provincial hospitals, matched by facility ID
--   'name'       - the name states the type ("District Hospital", "Rural Hospital", "Clinic"...)
--   'id pattern' - hospital-level codes end in 0 + letter (e.g. ZW02010A); clinic codes end in digits
--   'manual'     - set by a person; never overwritten when this script is re-run
-- Replace with the official MoHCC / DHIS2 classification when it is available.
-- Safe to re-run.

CREATE DATABASE IF NOT EXISTS registry DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS registry.province (
  province_id    VARCHAR(16)  NOT NULL,
  province_name  VARCHAR(100) NOT NULL,
  PRIMARY KEY (province_id)
);

CREATE TABLE IF NOT EXISTS registry.district (
  district_id    VARCHAR(16)  NOT NULL,
  district_name  VARCHAR(100) NOT NULL,
  province_id    VARCHAR(16)  NOT NULL,
  PRIMARY KEY (district_id),
  KEY idx_district_province (province_id)
);

CREATE TABLE IF NOT EXISTS registry.facility_type (
  type_code   VARCHAR(32)  NOT NULL,
  type_label  VARCHAR(100) NOT NULL,
  type_group  VARCHAR(20)  NOT NULL,
  sort_order  INT          NOT NULL,
  PRIMARY KEY (type_code)
);

INSERT INTO registry.facility_type (type_code, type_label, type_group, sort_order) VALUES
  ('CLINIC',              'Clinic',              'Clinic',   1),
  ('RURAL_HOSPITAL',      'Rural hospital',      'Hospital', 2),
  ('DISTRICT_HOSPITAL',   'District hospital',   'Hospital', 3),
  ('PROVINCIAL_HOSPITAL', 'Provincial hospital', 'Hospital', 4),
  ('CENTRAL_HOSPITAL',    'Central hospital',    'Hospital', 5),
  ('OTHER_HOSPITAL',      'Other hospital',      'Hospital', 6)
ON DUPLICATE KEY UPDATE type_label = VALUES(type_label), type_group = VALUES(type_group), sort_order = VALUES(sort_order);

CREATE TABLE IF NOT EXISTS registry.facility_registry (
  facility_id    VARCHAR(32)   NOT NULL,
  facility_name  VARCHAR(255)  NOT NULL,
  latitude       DECIMAL(9,6)  NULL,
  longitude      DECIMAL(9,6)  NULL,
  created_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (facility_id)
);
-- Columns added for the national registry (the first version had only id, name and coordinates).
ALTER TABLE registry.facility_registry
  ADD COLUMN IF NOT EXISTS province_id          VARCHAR(16)  NULL AFTER facility_name,
  ADD COLUMN IF NOT EXISTS district_id          VARCHAR(16)  NULL AFTER province_id,
  ADD COLUMN IF NOT EXISTS facility_type        VARCHAR(32)  NULL AFTER district_id,
  ADD COLUMN IF NOT EXISTS facility_type_source VARCHAR(20)  NULL AFTER facility_type,
  ADD COLUMN IF NOT EXISTS dhis2_code           VARCHAR(32)  NULL AFTER longitude,
  ADD COLUMN IF NOT EXISTS impilo_code          VARCHAR(32)  NULL AFTER dhis2_code,
  ADD INDEX IF NOT EXISTS idx_facility_province (province_id),
  ADD INDEX IF NOT EXISTS idx_facility_district (district_id),
  ADD INDEX IF NOT EXISTS idx_facility_type (facility_type);

INSERT INTO registry.province (province_id, province_name)
SELECT province_id, TRIM(REPLACE(name, ' Province', '')) FROM facility.province
ON DUPLICATE KEY UPDATE province_name = VALUES(province_name);

INSERT INTO registry.district (district_id, district_name, province_id)
SELECT district_id, TRIM(name), province_id FROM facility.district
ON DUPLICATE KEY UPDATE district_name = VALUES(district_name), province_id = VALUES(province_id);

INSERT INTO registry.facility_registry
  (facility_id, facility_name, province_id, district_id, facility_type, facility_type_source,
   latitude, longitude, dhis2_code, impilo_code)
SELECT
  f.facility_id,
  TRIM(f.name),
  COALESCE(d.province_id, f.province_id),
  d.district_id,
  CASE
    WHEN f.facility_id IN ('ZW000C0C','ZW000D0D','ZW000E0E','ZW090B0B','ZW090C0C','ZW090D0D') THEN 'CENTRAL_HOSPITAL'
    WHEN f.facility_id IN ('ZW01050A','ZW02010A','ZW03050A','ZW04050A','ZW06030A','ZW07030A','ZW08050A') THEN 'PROVINCIAL_HOSPITAL'
    WHEN f.name REGEXP 'district hosp' THEN 'DISTRICT_HOSPITAL'
    WHEN f.name REGEXP 'rural hosp|mission hosp' THEN 'RURAL_HOSPITAL'
    WHEN f.name REGEXP 'provincial hosp|prov\\. hosp|p\\.h$' THEN 'PROVINCIAL_HOSPITAL'
    WHEN f.name REGEXP 'hosp' THEN 'OTHER_HOSPITAL'
    WHEN BINARY f.facility_id REGEXP '^ZW0[09][0-9A-Z]{2}[0O][A-Z]$' THEN 'OTHER_HOSPITAL'   -- Harare/Bulawayo city hospitals (private, specialist)
    WHEN BINARY f.facility_id REGEXP '^ZW0[1-8][0-9]{2}0A$' THEN 'DISTRICT_HOSPITAL'          -- main hospital of a rural district
    WHEN BINARY f.facility_id REGEXP '[0O][A-Z]$' THEN 'RURAL_HOSPITAL'                       -- mission and rural hospitals
    ELSE 'CLINIC'                                                                             -- clinics, polyclinics, health centres
  END,
  CASE
    WHEN f.facility_id IN ('ZW000C0C','ZW000D0D','ZW000E0E','ZW090B0B','ZW090C0C','ZW090D0D',
                           'ZW01050A','ZW02010A','ZW03050A','ZW04050A','ZW06030A','ZW07030A','ZW08050A') THEN 'known list'
    WHEN f.name REGEXP 'hosp' THEN 'name'
    ELSE 'id pattern'
  END,
  CASE WHEN f.latitude BETWEEN -22.5 AND -15.5 AND f.longitude BETWEEN 25 AND 33.2 THEN f.latitude END,
  CASE WHEN f.latitude BETWEEN -22.5 AND -15.5 AND f.longitude BETWEEN 25 AND 33.2 THEN f.longitude END,
  NULLIF(TRIM(f.dhis_code), ''),
  NULLIF(TRIM(f.impilo_code), '')
FROM facility.facility f
LEFT JOIN facility.district d ON d.district_id = f.district_id
ON DUPLICATE KEY UPDATE
  facility_type        = IF(facility_type_source = 'manual', facility_type, VALUES(facility_type)),
  facility_type_source = IF(facility_type_source = 'manual', 'manual', VALUES(facility_type_source)),
  facility_name        = VALUES(facility_name),
  province_id          = VALUES(province_id),
  district_id          = VALUES(district_id),
  latitude             = VALUES(latitude),
  longitude            = VALUES(longitude),
  dhis2_code           = VALUES(dhis2_code),
  impilo_code          = VALUES(impilo_code);

GRANT SELECT ON registry.province          TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON registry.district          TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON registry.facility_type     TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON registry.facility_registry TO 'attendance_api'@'127.0.0.1';

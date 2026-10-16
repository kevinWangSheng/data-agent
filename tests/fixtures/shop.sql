-- Seam fixture for the acceptance tests (ticket 01): a small shop with dirty keys and enum values.
-- Portable across DuckDB and Postgres. Source tables live in schema raw; the unqualified tables
-- below them are what the mini dbt project in tests/fixtures/dbt_shop/ materializes, plus two
-- deliberately broken builds (customer_orders_fanout, orders_enriched) for the verifier tools.
-- The tests build a fresh DuckDB file from this script for every test (a committed .duckdb file
-- would be 2.6 MB, over the pre-commit size limit); Postgres variants load it into a scratch database.

DROP TABLE IF EXISTS orders_enriched;
DROP TABLE IF EXISTS customer_orders_fanout;
DROP TABLE IF EXISTS customer_orders;
DROP TABLE IF EXISTS stg_orders;
DROP TABLE IF EXISTS stg_customers;
DROP SCHEMA IF EXISTS raw CASCADE;
CREATE SCHEMA raw;

CREATE TABLE raw.customers (customer_id VARCHAR, customer_name VARCHAR, tier VARCHAR, signup_date DATE);
INSERT INTO raw.customers VALUES
  ('C0001', 'Avery Lane', 'Gold', DATE '2025-01-06'),
  ('C0002', 'Blake Moss', 'GOLD', DATE '2025-01-17'),
  ('C0003', 'Casey Reed', 'gold_tier', DATE '2025-01-28'),
  ('C0004', 'Devon Hale', 'Silver', DATE '2025-02-08'),
  ('C0005', 'Emery Cole', 'silver', DATE '2025-02-19'),
  ('C0006', 'Finley Shaw', 'Bronze', DATE '2025-03-02'),
  ('C0007', 'Gray Holt', 'Bronze', DATE '2025-03-13'),
  ('C0008', 'Harper Voss', 'Platinum', DATE '2025-03-24'),
  ('C0009', 'Indy Park', 'Silver', DATE '2025-04-04'),
  ('C0010', 'Jordan Wade', 'Bronze', DATE '2025-04-15'),
  ('C0011', 'Kai Brooks', 'Gold', DATE '2025-04-26'),
  ('C0012', 'Logan Pierce', 'Bronze', DATE '2025-05-07');

-- customer_ref is the dirty key: the same customer appears as C0001, c0001, ' C0001' and 'C-0001 '.
CREATE TABLE raw.orders (order_id INTEGER, customer_ref VARCHAR, status VARCHAR, amount DECIMAL(10, 2), order_date DATE);
INSERT INTO raw.orders VALUES
  (1, 'c0001', 'Completed', 19.75, DATE '2025-03-06'),
  (2, ' C0001', 'COMPLETE', 27.00, DATE '2025-03-11'),
  (3, 'C-0001 ', 'returned', 34.25, DATE '2025-03-16'),
  (4, 'C0001', 'pending', 41.50, DATE '2025-03-21'),
  (5, 'c0001', 'completed', 48.75, DATE '2025-03-26'),
  (6, ' C0001', 'completed', 56.00, DATE '2025-03-31'),
  (7, 'C-0001 ', 'Completed', 63.25, DATE '2025-04-05'),
  (8, 'C0001', 'COMPLETE', 70.50, DATE '2025-04-10'),
  (9, 'c0001', 'returned', 77.75, DATE '2025-04-15'),
  (10, ' C0002', 'pending', 85.00, DATE '2025-04-20'),
  (11, 'C-0002 ', 'completed', 92.25, DATE '2025-04-25'),
  (12, 'C0002', 'completed', 99.50, DATE '2025-04-30'),
  (13, 'c0002', 'Completed', 16.75, DATE '2025-05-05'),
  (14, ' C0002', 'COMPLETE', 24.00, DATE '2025-05-10'),
  (15, 'C-0003 ', 'returned', 31.25, DATE '2025-05-15'),
  (16, 'C0003', 'pending', 38.50, DATE '2025-05-20'),
  (17, 'c0003', 'completed', 45.75, DATE '2025-05-25'),
  (18, ' C0003', 'completed', 53.00, DATE '2025-05-30'),
  (19, 'C-0004 ', 'Completed', 60.25, DATE '2025-06-04'),
  (20, 'C0004', 'COMPLETE', 67.50, DATE '2025-06-09'),
  (21, 'c0004', 'returned', 74.75, DATE '2025-06-14'),
  (22, ' C0005', 'pending', 82.00, DATE '2025-06-19'),
  (23, 'C-0005 ', 'completed', 89.25, DATE '2025-06-24'),
  (24, 'C0005', 'completed', 96.50, DATE '2025-06-29'),
  (25, 'c0006', 'Completed', 13.75, DATE '2025-07-04'),
  (26, ' C0006', 'COMPLETE', 21.00, DATE '2025-07-09'),
  (27, 'C-0006 ', 'returned', 28.25, DATE '2025-07-14'),
  (28, 'C0007', 'pending', 35.50, DATE '2025-07-19'),
  (29, 'c0007', 'completed', 42.75, DATE '2025-07-24'),
  (30, ' C0008', 'completed', 50.00, DATE '2025-07-29'),
  (31, 'C-0008 ', 'Completed', 57.25, DATE '2025-08-03'),
  (32, 'C0009', 'COMPLETE', 64.50, DATE '2025-08-08'),
  (33, 'c0009', 'returned', 71.75, DATE '2025-08-13'),
  (34, ' C0010', 'pending', 79.00, DATE '2025-08-18'),
  (35, 'C-0010 ', 'completed', 86.25, DATE '2025-08-23'),
  (36, 'C0011', 'completed', 93.50, DATE '2025-08-28'),
  (37, 'c0012', 'Completed', 100.75, DATE '2025-09-02');

-- Exactly two items per order, so joining items onto orders doubles the row count (37 -> 74).
CREATE TABLE raw.order_items (order_item_id INTEGER, order_id INTEGER, product VARCHAR, quantity INTEGER);
INSERT INTO raw.order_items VALUES
  (1, 1, 'gadget', 2),
  (2, 1, 'gizmo', 3),
  (3, 2, 'gizmo', 3),
  (4, 2, 'doohickey', 1),
  (5, 3, 'doohickey', 1),
  (6, 3, 'widget', 2),
  (7, 4, 'widget', 2),
  (8, 4, 'gadget', 3),
  (9, 5, 'gadget', 3),
  (10, 5, 'gizmo', 1),
  (11, 6, 'gizmo', 1),
  (12, 6, 'doohickey', 2),
  (13, 7, 'doohickey', 2),
  (14, 7, 'widget', 3),
  (15, 8, 'widget', 3),
  (16, 8, 'gadget', 1),
  (17, 9, 'gadget', 1),
  (18, 9, 'gizmo', 2),
  (19, 10, 'gizmo', 2),
  (20, 10, 'doohickey', 3),
  (21, 11, 'doohickey', 3),
  (22, 11, 'widget', 1),
  (23, 12, 'widget', 1),
  (24, 12, 'gadget', 2),
  (25, 13, 'gadget', 2),
  (26, 13, 'gizmo', 3),
  (27, 14, 'gizmo', 3),
  (28, 14, 'doohickey', 1),
  (29, 15, 'doohickey', 1),
  (30, 15, 'widget', 2),
  (31, 16, 'widget', 2),
  (32, 16, 'gadget', 3),
  (33, 17, 'gadget', 3),
  (34, 17, 'gizmo', 1),
  (35, 18, 'gizmo', 1),
  (36, 18, 'doohickey', 2),
  (37, 19, 'doohickey', 2),
  (38, 19, 'widget', 3),
  (39, 20, 'widget', 3),
  (40, 20, 'gadget', 1),
  (41, 21, 'gadget', 1),
  (42, 21, 'gizmo', 2),
  (43, 22, 'gizmo', 2),
  (44, 22, 'doohickey', 3),
  (45, 23, 'doohickey', 3),
  (46, 23, 'widget', 1),
  (47, 24, 'widget', 1),
  (48, 24, 'gadget', 2),
  (49, 25, 'gadget', 2),
  (50, 25, 'gizmo', 3),
  (51, 26, 'gizmo', 3),
  (52, 26, 'doohickey', 1),
  (53, 27, 'doohickey', 1),
  (54, 27, 'widget', 2),
  (55, 28, 'widget', 2),
  (56, 28, 'gadget', 3),
  (57, 29, 'gadget', 3),
  (58, 29, 'gizmo', 1),
  (59, 30, 'gizmo', 1),
  (60, 30, 'doohickey', 2),
  (61, 31, 'doohickey', 2),
  (62, 31, 'widget', 3),
  (63, 32, 'widget', 3),
  (64, 32, 'gadget', 1),
  (65, 33, 'gadget', 1),
  (66, 33, 'gizmo', 2),
  (67, 34, 'gizmo', 2),
  (68, 34, 'doohickey', 3),
  (69, 35, 'doohickey', 3),
  (70, 35, 'widget', 1),
  (71, 36, 'widget', 1),
  (72, 36, 'gadget', 2),
  (73, 37, 'gadget', 2),
  (74, 37, 'gizmo', 3);

-- A wide table: its full column list is far longer than a 200-character summary.
CREATE TABLE raw.web_events (
  event_id INTEGER,
  session_id VARCHAR,
  customer_id VARCHAR,
  event_name VARCHAR,
  event_category VARCHAR,
  event_label VARCHAR,
  event_value INTEGER,
  page_url VARCHAR,
  page_title VARCHAR,
  referrer_url VARCHAR,
  utm_source VARCHAR,
  utm_medium VARCHAR,
  utm_campaign VARCHAR,
  utm_term VARCHAR,
  utm_content VARCHAR,
  device_type VARCHAR,
  os_name VARCHAR,
  os_version VARCHAR,
  browser_name VARCHAR,
  browser_version VARCHAR,
  screen_width INTEGER,
  screen_height INTEGER,
  viewport_width INTEGER,
  viewport_height INTEGER,
  geo_country VARCHAR,
  geo_region VARCHAR,
  geo_city VARCHAR,
  geo_postal_code VARCHAR,
  ip_hash VARCHAR,
  user_agent_hash VARCHAR,
  page_load_ms INTEGER,
  time_on_page_s INTEGER,
  scroll_depth_pct INTEGER,
  is_bounce VARCHAR,
  is_new_visitor VARCHAR,
  cart_value VARCHAR,
  items_in_cart INTEGER,
  experiment_bucket VARCHAR,
  consent_status VARCHAR,
  created_at VARCHAR
);
INSERT INTO raw.web_events VALUES
  (18, 'session_id_1', 'C0001', 'event_name_1', 'event_category_1', 'event_label_1', 21, 'page_url_1', 'page_title_1', 'referrer_url_1', 'utm_source_1', 'utm_medium_1', 'utm_campaign_1', 'utm_term_1', 'utm_content_1', 'device_type_1', 'os_name_1', 'os_version_1', 'browser_name_1', 'browser_version_1', 22, 23, 24, 25, 'geo_country_1', 'geo_region_1', 'geo_city_1', 'geo_postal_code_1', 'ip_hash_1', 'user_agent_hash_1', 22, 24, 26, 'is_bounce_1', 'is_new_visitor_1', 'cart_value_1', 23, 'experiment_bucket_1', 'consent_status_1', 'created_at_1'),
  (28, 'session_id_2', 'C0002', 'event_name_2', 'event_category_2', 'event_label_2', 31, 'page_url_2', 'page_title_2', 'referrer_url_2', 'utm_source_2', 'utm_medium_2', 'utm_campaign_2', 'utm_term_2', 'utm_content_2', 'device_type_2', 'os_name_2', 'os_version_2', 'browser_name_2', 'browser_version_2', 32, 33, 34, 35, 'geo_country_2', 'geo_region_2', 'geo_city_2', 'geo_postal_code_2', 'ip_hash_2', 'user_agent_hash_2', 32, 34, 36, 'is_bounce_2', 'is_new_visitor_2', 'cart_value_2', 33, 'experiment_bucket_2', 'consent_status_2', 'created_at_2'),
  (38, 'session_id_3', 'C0003', 'event_name_3', 'event_category_3', 'event_label_3', 41, 'page_url_3', 'page_title_3', 'referrer_url_3', 'utm_source_3', 'utm_medium_3', 'utm_campaign_3', 'utm_term_3', 'utm_content_3', 'device_type_3', 'os_name_3', 'os_version_3', 'browser_name_3', 'browser_version_3', 42, 43, 44, 45, 'geo_country_3', 'geo_region_3', 'geo_city_3', 'geo_postal_code_3', 'ip_hash_3', 'user_agent_hash_3', 42, 44, 46, 'is_bounce_3', 'is_new_visitor_3', 'cart_value_3', 43, 'experiment_bucket_3', 'consent_status_3', 'created_at_3');

-- What the dbt project builds (same SQL as its models).
CREATE TABLE stg_customers AS
SELECT upper(trim(customer_id)) AS customer_id, customer_name, tier, signup_date FROM raw.customers;

CREATE TABLE stg_orders AS
SELECT order_id, upper(replace(trim(customer_ref), '-', '')) AS customer_id, status, amount, order_date FROM raw.orders;

CREATE TABLE customer_orders AS
SELECT c.customer_id, c.customer_name, count(o.order_id) AS n_orders,
       coalesce(sum(o.amount), 0) AS total_amount
FROM stg_customers c LEFT JOIN stg_orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.customer_name;

-- Broken: joins items before aggregating, so every count and sum is doubled.
CREATE TABLE customer_orders_fanout AS
SELECT c.customer_id, c.customer_name, count(i.order_item_id) AS n_orders,
       coalesce(sum(o.amount), 0) AS total_amount
FROM stg_customers c
LEFT JOIN stg_orders o ON o.customer_id = c.customer_id
LEFT JOIN raw.order_items i ON i.order_id = o.order_id
GROUP BY c.customer_id, c.customer_name;

-- Broken: meant to be one row per order, but has one row per order item (74 rows).
CREATE TABLE orders_enriched AS
SELECT o.order_id, o.customer_id, o.status, o.amount, i.product, i.quantity
FROM stg_orders o JOIN raw.order_items i ON i.order_id = o.order_id;

-- Orders database for the chat acceptance tests (ticket 88, tests/acceptance/test_chat_e2e.py).
-- One of the two data sources the chat backend is given; the customers live in the other one
-- (customers.sql), so a question about regions needs both. Written for these tests, not taken from
-- any benchmark (ADR-0005).
--
-- The ambiguity that changes a result: a refunded order stays a row here with its full amount, so
-- "revenue" differs by whether refunded orders count (gross) or not (net).

DROP SCHEMA IF EXISTS sales CASCADE;
CREATE SCHEMA sales;

CREATE TABLE sales.orders (
  order_id INTEGER,
  customer_id VARCHAR,
  order_date DATE,
  amount DECIMAL(10, 2),
  status VARCHAR  -- 'completed' or 'refunded'
);
INSERT INTO sales.orders VALUES
  (1001, 'C01', DATE '2023-01-28', 57.25, 'completed'),
  (1002, 'C01', DATE '2023-02-16', 94.50, 'completed'),
  (1003, 'C01', DATE '2023-03-07', 131.75, 'refunded'),
  (1004, 'C01', DATE '2023-03-26', 168.00, 'completed'),
  (1005, 'C01', DATE '2023-04-14', 25.25, 'completed'),
  (1006, 'C01', DATE '2023-05-03', 62.50, 'refunded'),
  (1007, 'C01', DATE '2023-05-22', 99.75, 'completed'),
  (1008, 'C01', DATE '2023-06-10', 136.00, 'completed'),
  (1009, 'C01', DATE '2023-06-29', 173.25, 'refunded'),
  (1010, 'C02', DATE '2023-07-18', 30.50, 'completed'),
  (1011, 'C02', DATE '2023-08-06', 67.75, 'completed'),
  (1012, 'C02', DATE '2023-08-25', 104.00, 'refunded'),
  (1013, 'C02', DATE '2023-09-13', 141.25, 'completed'),
  (1014, 'C02', DATE '2023-10-02', 178.50, 'completed'),
  (1015, 'C02', DATE '2023-10-21', 35.75, 'refunded'),
  (1016, 'C02', DATE '2023-11-09', 72.00, 'completed'),
  (1017, 'C03', DATE '2023-11-28', 109.25, 'completed'),
  (1018, 'C03', DATE '2023-12-17', 146.50, 'completed'),
  (1019, 'C03', DATE '2024-01-05', 183.75, 'completed'),
  (1020, 'C03', DATE '2024-01-24', 40.00, 'refunded'),
  (1021, 'C03', DATE '2024-02-12', 77.25, 'completed'),
  (1022, 'C03', DATE '2024-03-02', 114.50, 'completed'),
  (1023, 'C04', DATE '2024-03-21', 151.75, 'refunded'),
  (1024, 'C04', DATE '2024-04-09', 188.00, 'completed'),
  (1025, 'C04', DATE '2024-04-28', 45.25, 'completed'),
  (1026, 'C04', DATE '2024-05-17', 82.50, 'refunded'),
  (1027, 'C04', DATE '2024-06-05', 119.75, 'completed'),
  (1028, 'C05', DATE '2024-06-24', 156.00, 'completed'),
  (1029, 'C05', DATE '2024-07-13', 193.25, 'refunded'),
  (1030, 'C05', DATE '2024-08-01', 50.50, 'completed'),
  (1031, 'C05', DATE '2024-08-20', 87.75, 'completed'),
  (1032, 'C06', DATE '2024-09-08', 124.00, 'refunded'),
  (1033, 'C06', DATE '2024-09-27', 161.25, 'completed'),
  (1034, 'C06', DATE '2024-10-16', 198.50, 'completed'),
  (1035, 'C07', DATE '2024-11-04', 55.75, 'refunded'),
  (1036, 'C07', DATE '2024-11-23', 92.00, 'completed'),
  (1037, 'C08', DATE '2023-01-12', 129.25, 'completed');

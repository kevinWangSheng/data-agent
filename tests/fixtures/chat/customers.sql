-- Customers database for the chat acceptance tests (ticket 88, tests/acceptance/test_chat_e2e.py).
-- The other data source holds the orders (orders.sql); customer_id joins the two. Written for these
-- tests, not taken from any benchmark (ADR-0005).

DROP SCHEMA IF EXISTS crm CASCADE;
CREATE SCHEMA crm;

CREATE TABLE crm.customers (
  customer_id VARCHAR,
  name VARCHAR,
  region VARCHAR,
  signup_date DATE
);
INSERT INTO crm.customers VALUES
  ('C01', 'Avery Lane', 'North', DATE '2022-11-02'),
  ('C02', 'Blake Moss', 'South', DATE '2022-11-15'),
  ('C03', 'Casey Reed', 'East', DATE '2022-12-01'),
  ('C04', 'Devon Hale', 'West', DATE '2022-12-20'),
  ('C05', 'Emery Cole', 'North', DATE '2023-01-04'),
  ('C06', 'Finley Shaw', 'South', DATE '2023-01-19'),
  ('C07', 'Gray Holt', 'East', DATE '2023-02-07'),
  ('C08', 'Harper Voss', 'West', DATE '2023-02-26'),
  ('C09', 'Indy Park', 'North', DATE '2023-03-14'),
  ('C10', 'Jordan Wade', 'East', DATE '2023-04-02'),
  ('C11', 'Kai Brooks', 'East', DATE '2023-05-21'),
  ('C12', 'Logan Pierce', 'West', DATE '2023-06-09'),
  ('C13', 'Morgan Tate', 'West', DATE '2023-07-30'),
  ('C14', 'Noel Grant', 'West', DATE '2023-09-12');

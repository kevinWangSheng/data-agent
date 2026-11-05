-- Built models for the verifier v2 acceptance tests (ticket 44), as dbt run would leave them from
-- project_after/, plus cmp_model for compare_by_key. Tests build this into their own DuckDB file.

-- stg_orders as built from project_after: the agent dropped the `status` column, which the
-- snapshot YML (project_before/models/schema.yml) still declares.
CREATE TABLE stg_orders (order_id INTEGER, customer_id VARCHAR, amount DECIMAL(10,2), order_date DATE);
INSERT INTO stg_orders VALUES
  (1, 'C1', 10.00, DATE '2024-01-05'),
  (2, 'C1', 20.00, DATE '2024-01-05'),
  (3, 'C2', 30.00, DATE '2024-01-06'),
  (4, NULL, 40.00, DATE '2024-01-07');

-- customer_ltv: one row has a NULL grain key.
CREATE TABLE customer_ltv (customer_id VARCHAR, lifetime_value DECIMAL(10,2), bonus_col INTEGER);
INSERT INTO customer_ltv VALUES ('C1', 30.00, 0), ('C2', 30.00, 0), (NULL, 40.00, 0);

-- daily_sales: store_id repeats; (store_id, sale_date) is unique.
CREATE TABLE daily_sales (store_id INTEGER, sale_date DATE, revenue DECIMAL(10,2), running_revenue DECIMAL(10,2));
INSERT INTO daily_sales VALUES
  (1, DATE '2024-01-01', 100.00, 100.00),
  (1, DATE '2024-01-02', 50.00, 150.00),
  (2, DATE '2024-01-01', 75.00, 75.00);

-- daily_returns: same shape as daily_sales; its finer grain is declared only in the agent's YML.
CREATE TABLE daily_returns (store_id INTEGER, return_date DATE, qty INTEGER);
INSERT INTO daily_returns VALUES
  (1, DATE '2024-01-01', 2),
  (1, DATE '2024-01-02', 1),
  (2, DATE '2024-01-01', 3);

-- ranked_orders: customer C1 has two orders on the same date, so ORDER BY order_date alone (the
-- agent's edit) is not a unique ordering inside the partition.
CREATE TABLE ranked_orders (customer_id VARCHAR, order_id INTEGER, order_date DATE, amount DECIMAL(10,2), rn INTEGER);
INSERT INTO ranked_orders VALUES
  ('C1', 1, DATE '2024-01-05', 10.00, 1),
  ('C1', 2, DATE '2024-01-05', 20.00, 2),
  ('C2', 3, DATE '2024-01-06', 30.00, 1);

-- cmp_model: one column per equality rule of compare_by_key.
CREATE TABLE cmp_model (
  k INTEGER, dec_v DECIMAL(10,2), dbl_v DOUBLE, int_v INTEGER, str_v VARCHAR, day_v DATE, bool_v BOOLEAN
);
INSERT INTO cmp_model VALUES
  (1, 12.35, 1000000.0, 5, 'a', DATE '2024-01-01', true),
  (2, 0.10, 0.0, 7, 'b', DATE '2024-02-02', false),
  (3, 99.99, 1e-9, NULL, NULL, DATE '2024-03-03', true);

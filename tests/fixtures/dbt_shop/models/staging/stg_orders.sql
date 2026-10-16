SELECT order_id, upper(replace(trim(customer_ref), '-', '')) AS customer_id, status, amount, order_date
FROM {{ source('raw', 'orders') }}

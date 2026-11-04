SELECT order_id, customer_id, status, amount, order_date
FROM {{ source('raw', 'orders') }}
WHERE status <> 'cancelled' AND amount > 0

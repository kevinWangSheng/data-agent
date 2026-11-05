SELECT order_id, customer_id, amount, order_date
FROM {{ source('raw', 'orders') }}
WHERE amount > 0

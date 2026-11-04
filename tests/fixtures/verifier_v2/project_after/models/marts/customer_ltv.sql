SELECT customer_id, sum(amount) AS lifetime_value, 0 AS bonus_col
FROM {{ ref('stg_orders') }}
GROUP BY customer_id

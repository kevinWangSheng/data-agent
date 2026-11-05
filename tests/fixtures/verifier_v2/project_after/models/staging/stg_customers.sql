SELECT customer_id, customer_name
FROM {{ source('raw', 'customers_v2') }}

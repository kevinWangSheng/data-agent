SELECT upper(trim(customer_id)) AS customer_id, customer_name, tier, signup_date
FROM {{ source('raw', 'customers') }}

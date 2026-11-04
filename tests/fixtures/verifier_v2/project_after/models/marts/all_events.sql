SELECT event_id, user_id FROM {{ source('raw', 'events_web') }}
UNION ALL
SELECT event_id, user_id FROM {{ source('raw', 'events_app') }}

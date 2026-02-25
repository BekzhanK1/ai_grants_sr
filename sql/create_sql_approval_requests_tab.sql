-- Заявки на исполнение SQL для аппрува админом.
-- Сохраняем то, что написал пользователь (user_prompt, business_reason), чтобы админ мог прочитать и решить.

CREATE SCHEMA IF NOT EXISTS ai_admin;

CREATE TABLE IF NOT EXISTS ai_admin.sql_approval_requests_tab (
    request_id       bigserial PRIMARY KEY,
    request_type     text NOT NULL,              -- 'grants' | 'user_creation'
    user_prompt      text,                       -- что написал пользователь (запрос)
    business_reason  text,                       -- причина / обоснование
    sql_queries      jsonb NOT NULL,             -- массив SQL-строк
    status           text NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | executed | failed
    created_by       int NULL,                   -- employee_id инициатора
    created_at       timestamptz NOT NULL DEFAULT now(),
    approved_by      int,
    approved_at      timestamptz,
    comment          text
);

COMMENT ON TABLE ai_admin.sql_approval_requests_tab IS 'Заявки на выполнение SQL (гранты, создание пользователя). Админ читает user_prompt и business_reason и решает — выполнять или отклонить.';

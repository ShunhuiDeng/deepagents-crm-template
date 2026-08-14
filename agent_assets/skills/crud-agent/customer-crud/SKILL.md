---
name: customer-crud
description: Use when a delegated task needs to select, insert, or update Intelligent CRM business records safely.
---

# Intelligent CRM five-entity data operations

The real business entities are `lead`, `account`, `contact`, `opportunity`, and
`activity`. Visibility comes from the authenticated account's role. Never infer
or accept a user identity from model input.

## Safety rules

- Search with the matching `select_*` tool before creating, updating, or converting.
- Use the exact table field names declared by each tool; never use legacy aliases.
- Resolve every foreign-key UUID with the matching select tool before staging a write.
- Never supply `owner_id` or `assigned_user_id`. Ownership and assignment are fixed
  by the authenticated user's role and enforced by the repository.
- Insert creates a pending action. It is not a database write until the logged-in user approves it in the UI.
- Resolve ambiguous names and obtain one exact entity UUID before updating.
- Update only fields explicitly included in the delegated task.
- Update also creates a pending action and is not complete until approved in the UI.
- Deletion is not an Agent capability.
- Sales may access only rows owned by or assigned to the signed-in user. Never try
  to bypass that boundary through an account, contact, lead, or opportunity link.
- Never read or infer another conversation's messages or conversation memory.
- Treat tool output as authoritative; never invent success or an entity ID.
- Use `select_account_overview` for an account's contacts, opportunities, activities,
  and originating lead conversions. Use `select_activities` with the relevant foreign-key
  filter when only one linked entity's timeline is needed.
- A real lead conversion is exactly one `convert_lead` pending action and one approval.
  Never simulate it with separate account/contact inserts or a lead status update.
- Before `convert_lead`, resolve the unique lead ID. If linking existing records, also
  resolve `account_id` and/or `contact_id`. If neither target is supplied, the backend
  atomically derives a new account and contact from the lead. An optional opportunity
  may be included in that same conversion request.

Return selected entity IDs or the pending action ID and proposed fields concisely.

## Exact writable fields

- `lead`: `first_name`, `last_name`, `company_name`, `email`, `phone`,
  `job_title`, `source`, `status`, `score`, `description`, `extra`
- `account`: `name`, `industry`, `website`, `phone`, `email`, `address`, `city`,
  `state`, `country`, `employee_count`, `annual_revenue`, `status`, `source`,
  `description`
- `contact`: `account_id`, `first_name`, `last_name`, `title`, `department`,
  `email`, `phone`, `mobile`, `wechat`, `linkedin`, `source`, `description`
- `opportunity`: `account_id`, `name`, `amount`, `currency`, `stage`,
  `primary_contact_id`, `probability`, `expected_close_date`, `source`, `description`
- `activity`: `type`, `subject`, `description`, `status`, `priority`, `start_at`,
  `end_at`, `account_id`, `contact_id`, `lead_id`, `opportunity_id`

Tool mapping is one-to-one: `select_leads` / `insert_lead` / `update_lead`,
and the corresponding account, contact, opportunity, and activity variants.
`convert_lead` and `select_account_overview` are the two cross-entity workflow tools.

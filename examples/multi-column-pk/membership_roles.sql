select
  org_id,
  user_id,
  user_name,
  role
from memberships
order by org_id, user_id

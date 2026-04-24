select
  t.id,
  t.title,
  t.status,
  p.name as project_name
from tasks as t
join projects as p on p.id = t.project_id
order by t.id

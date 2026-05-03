# Multi-Column Primary Key Example

This project demonstrates a table whose identity is the pair of `org_id` and
`user_id`:

```yaml
primary_key: [org_id, user_id]
```

Try:

```bash
# (current dir: examples/multi-column-pk)
lnz list
lnz add membership_roles.sql
lnz list
lnz view membership_roles
lnz explain membership_roles
```

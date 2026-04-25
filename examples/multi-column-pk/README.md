# Multi-Column Primary Key Example

This project demonstrates a table whose identity is the pair of `org_id` and
`user_id`:

```yaml
primary_key: [org_id, user_id]
```

Try:

```bash
lnz list --project examples/multi-column-pk
lnz view membership_roles --project examples/multi-column-pk
lnz explain membership_roles --project examples/multi-column-pk
```

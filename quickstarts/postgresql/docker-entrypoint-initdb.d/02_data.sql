INSERT INTO analytics.users (email, full_name)
VALUES
  ('ada@example.com', 'Ada Lovelace'),
  ('grace@example.com', 'Grace Hopper'),
  ('linus@example.com', 'Linus Torvalds')
ON CONFLICT DO NOTHING;
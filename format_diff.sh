echo '```diff' > diff.md
git diff database.py >> diff.md
echo '```' >> diff.md
cat diff.md

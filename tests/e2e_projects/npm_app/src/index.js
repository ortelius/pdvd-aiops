const express = require('express');
const _ = require('lodash');
const axios = require('axios');

const app = express();

app.get('/', (req, res) => {
  res.json({ status: 'ok', message: 'E2E npm test app' });
});

app.get('/merge', (req, res) => {
  const defaults = { color: 'blue', size: 'medium' };
  const custom = req.query;
  const merged = _.merge({}, defaults, custom);
  res.json(merged);
});

app.get('/lodash-version', (req, res) => {
  res.json({ version: _.VERSION });
});

// Export for testing
module.exports = app;

// Start server only if run directly
if (require.main === module) {
  const PORT = process.env.PORT || 3000;
  app.listen(PORT, () => console.log(`Listening on ${PORT}`));
}

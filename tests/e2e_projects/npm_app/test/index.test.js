const request = require('supertest');
const app = require('../src/index');

describe('E2E npm app', () => {
  test('GET / returns status ok', async () => {
    const res = await request(app).get('/');
    expect(res.statusCode).toBe(200);
    expect(res.body.status).toBe('ok');
  });

  test('GET /merge returns merged object', async () => {
    const res = await request(app).get('/merge?color=red');
    expect(res.statusCode).toBe(200);
    expect(res.body.color).toBe('red');
    expect(res.body.size).toBe('medium');
  });

  test('GET /lodash-version returns a version string', async () => {
    const res = await request(app).get('/lodash-version');
    expect(res.statusCode).toBe(200);
    expect(res.body.version).toBeDefined();
    expect(typeof res.body.version).toBe('string');
  });
});

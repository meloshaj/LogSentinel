import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  setAuthToken,
  getAuthToken,
  clearAuthToken,
  isAuthTokenValid,
  getAuthErrorMessage,
} from '../../src/utils/auth';

describe('Remember Me (Auth Utils)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('keeps access JWT in memory when remember-me is checked', () => {
    // 31, 33, 34
    setAuthToken('test-token', true);
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
    expect(getAuthToken()).toBe('test-token');
  });

  it('keeps access JWT in memory when remember-me is unchecked', () => {
    // 32, 33, 34
    setAuthToken('test-token', false);
    expect(sessionStorage.getItem('authToken')).toBeNull();
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(getAuthToken()).toBe('test-token');
  });

  it('clearAuthToken() clears both storages', () => {
    // 35
    localStorage.setItem('authToken', 'local-token');
    sessionStorage.setItem('authToken', 'session-token');
    clearAuthToken();
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
    expect(getAuthToken()).toBeNull();
  });

  it('Duplicate stale tokens are removed before storing', () => {
    // 36
    localStorage.setItem('authToken', 'old-local-token');
    sessionStorage.setItem('authToken', 'old-session-token');
    setAuthToken('new-token', true);
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
    
    // Now switch to session
    setAuthToken('newer-token', false);
    expect(sessionStorage.getItem('authToken')).toBeNull();
    expect(localStorage.getItem('authToken')).toBeNull();
  });

  it('does not persist callers without Remember Me', () => {
    setAuthToken('registration-token');
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
  });

  it('ignores and removes legacy browser tokens', () => {
    localStorage.setItem('authToken', 'legacy-local-token');
    sessionStorage.setItem('authToken', 'session-token');
    setAuthToken('memory-token');
    expect(getAuthToken()).toBe('memory-token');
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
  });

  it('clears the legacy login flag with every session reset', () => {
    localStorage.setItem('isLoggedIn', 'true');
    clearAuthToken();
    expect(localStorage.getItem('isLoggedIn')).toBeNull();
  });

  it('accepts only an unexpired numeric exp claim for route protection', () => {
    const encode = (payload: object) =>
      btoa(JSON.stringify(payload))
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/, '');
    const future = Math.floor(Date.now() / 1000) + 60;
    const past = Math.floor(Date.now() / 1000) - 60;

    expect(isAuthTokenValid(`header.${encode({ exp: future })}.signature`)).toBe(true);
    expect(isAuthTokenValid(`header.${encode({ exp: past })}.signature`)).toBe(false);
    expect(isAuthTokenValid(`header.${encode({ exp: String(future) })}.signature`)).toBe(false);
    expect(isAuthTokenValid(`header.${encode({ sub: 'user' })}.signature`)).toBe(false);
    expect(isAuthTokenValid('not-a-jwt')).toBe(false);
    expect(isAuthTokenValid(null)).toBe(false);
  });

  it('does not surface credential-bearing infrastructure details to the UI', async () => {
    const response = new Response(
      JSON.stringify({
        detail: 'database failed postgresql://user:SENTINEL_PASSWORD@db/logsentinel token=SENTINEL_TOKEN',
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } },
    );

    await expect(getAuthErrorMessage(response, 'Authentication is temporarily unavailable')).resolves.toBe(
      'Authentication is temporarily unavailable',
    );
  });
});

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  setAuthToken,
  getAuthToken,
  clearAuthToken,
  isAuthTokenValid,
} from '../../src/utils/auth';

describe('Remember Me (Auth Utils)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('Email/Google/Microsoft checked stores internal JWT in localStorage', () => {
    // 31, 33, 34
    setAuthToken('test-token', true);
    expect(localStorage.getItem('authToken')).toBe('test-token');
    expect(sessionStorage.getItem('authToken')).toBeNull();
    expect(getAuthToken()).toBe('test-token');
  });

  it('Email/Google/Microsoft unchecked stores internal JWT in sessionStorage', () => {
    // 32, 33, 34
    setAuthToken('test-token', false);
    expect(sessionStorage.getItem('authToken')).toBe('test-token');
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
    // local should have new, session should be cleared
    expect(localStorage.getItem('authToken')).toBe('new-token');
    expect(sessionStorage.getItem('authToken')).toBeNull();
    
    // Now switch to session
    setAuthToken('newer-token', false);
    expect(sessionStorage.getItem('authToken')).toBe('newer-token');
    expect(localStorage.getItem('authToken')).toBeNull();
  });

  it('preserves the legacy persistent default for callers without Remember Me', () => {
    setAuthToken('registration-token');
    expect(localStorage.getItem('authToken')).toBe('registration-token');
    expect(sessionStorage.getItem('authToken')).toBeNull();
  });

  it('reads legacy localStorage deterministically when both stores contain a token', () => {
    localStorage.setItem('authToken', 'legacy-local-token');
    sessionStorage.setItem('authToken', 'session-token');
    expect(getAuthToken()).toBe('legacy-local-token');
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
});

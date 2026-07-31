import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { setAuthToken, getAuthToken, clearAuthToken } from '../../src/utils/auth';

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
});

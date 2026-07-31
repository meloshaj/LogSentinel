import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi, Mock } from 'vitest';
import { useMicrosoftAuth } from '../../src/hooks/useMicrosoftAuth';
import { InteractionRequiredAuthError } from '@azure/msal-browser';

// Mock dependencies
vi.mock('../../src/config/msal', () => ({
  isMsalConfigured: vi.fn(() => true),
  loginRequest: { scopes: ['api://foo/access_as_user'] }
}));
vi.mock('@azure/msal-react', () => ({
  useMsal: vi.fn(),
}));
vi.mock('react-router', () => ({
  useNavigate: () => vi.fn(),
}));
vi.mock('../../src/utils/auth', () => ({
  setAuthToken: vi.fn(),
  clearAuthToken: vi.fn(),
}));

const mockMsal = {
  instance: {
    getActiveAccount: vi.fn(),
    setActiveAccount: vi.fn(),
    acquireTokenSilent: vi.fn(),
    acquireTokenPopup: vi.fn(),
    loginPopup: vi.fn(),
  },
  accounts: [],
};

import { useMsal } from '@azure/msal-react';
import { setAuthToken } from '../../src/utils/auth';

describe('useMicrosoftAuth flow', () => {
  let fetchMock: Mock;

  beforeEach(() => {
    vi.clearAllMocks();
    (useMsal as any).mockReturnValue(mockMsal);
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ access_token: 'fake-internal-jwt' })
    });
    window.fetch = fetchMock;
    
    // Default valid MSAL config env vars for tests
    vi.stubEnv('VITE_MICROSOFT_AUTH_ENABLED', 'true');
    vi.stubEnv('VITE_MICROSOFT_SPA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_MICROSOFT_AUTHORITY', 'https://login.microsoftonline.com/common');
    vi.stubEnv('VITE_MICROSOFT_API_SCOPE', 'api://foo/access_as_user');
    vi.stubEnv('VITE_MICROSOFT_REDIRECT_URI', 'http://localhost/redirect.html');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  const apiScope = 'api://foo/access_as_user';

  it('Existing active account triggers acquireTokenSilent', async () => {
    // 8, 9, 10, 13, 14, 21, 22, 23, 24, 25
    const account = { homeAccountId: '123' };
    mockMsal.instance.getActiveAccount.mockReturnValue(account);
    mockMsal.instance.acquireTokenSilent.mockResolvedValue({ accessToken: 'ms-token' });

    const { result } = renderHook(() => useMicrosoftAuth());
    
    let res;
    await act(async () => {
      res = await result.current.login(true);
    });

    expect(res).toEqual({ success: true });
    
    // Account set active if not already (in this mock it is already active, wait, homeAccountId matches)
    // Actually, hook calls getActiveAccount()?.homeAccountId !== account.homeAccountId
    
    expect(mockMsal.instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    
    // NOT called: loginPopup or acquireTokenPopup
    expect(mockMsal.instance.loginPopup).not.toHaveBeenCalled();
    expect(mockMsal.instance.acquireTokenPopup).not.toHaveBeenCalled();

    // Exact backend exchange
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/api/auth/microsoft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: 'ms-token' }) // 21, 22, 23
    });

    // Stored internal JWT
    expect(setAuthToken).toHaveBeenCalledWith('fake-internal-jwt', true); // 24
    
    // Loading state resets
    expect(result.current.loading).toBe(false);
  });

  it('InteractionRequiredAuthError triggers acquireTokenPopup', async () => {
    // 11
    const account = { homeAccountId: '123' };
    mockMsal.instance.getActiveAccount.mockReturnValue(account);
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(new InteractionRequiredAuthError('interaction_required', 'needs interaction'));
    mockMsal.instance.acquireTokenPopup.mockResolvedValue({ accessToken: 'ms-interactive-token', account });

    const { result } = renderHook(() => useMicrosoftAuth());
    
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.acquireTokenPopup).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    // The account is set active
    expect(mockMsal.instance.setActiveAccount).toHaveBeenCalledWith(account);
  });

  it('A non-interaction MSAL error does not trigger another popup', async () => {
    // 12
    const account = { homeAccountId: '123' };
    mockMsal.instance.getActiveAccount.mockReturnValue(account);
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(new Error('Random network error'));

    const { result } = renderHook(() => useMicrosoftAuth());
    
    let res;
    await act(async () => {
      res = await result.current.login(false);
    });

    expect(mockMsal.instance.acquireTokenPopup).not.toHaveBeenCalled();
    expect(mockMsal.instance.loginPopup).not.toHaveBeenCalled();
    expect(res).toEqual({ success: false, error: 'Microsoft authentication could not be verified' });
  });

  it('No account triggers loginPopup', async () => {
    // 15, 16, 17, 18
    mockMsal.instance.getActiveAccount.mockReturnValue(null);
    const newAccount = { homeAccountId: '456' };
    mockMsal.instance.loginPopup.mockResolvedValue({ accessToken: 'ms-new-token', account: newAccount });

    const { result } = renderHook(() => useMicrosoftAuth());
    
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.loginPopup).toHaveBeenCalledWith({
      scopes: [apiScope],
      prompt: 'select_account'
    });
    
    // Set active account
    expect(mockMsal.instance.setActiveAccount).toHaveBeenCalledWith(newAccount);
    
    // Exchanged with backend
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      body: JSON.stringify({ access_token: 'ms-new-token' })
    }));
  });

  it('Empty access token causes controlled recovery', async () => {
    // 19
    mockMsal.instance.getActiveAccount.mockReturnValue(null);
    mockMsal.instance.loginPopup.mockResolvedValue({ accessToken: '', account: { homeAccountId: '456' } });
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(new Error('No token'));

    const { result } = renderHook(() => useMicrosoftAuth());
    
    let res;
    await act(async () => {
      res = await result.current.login(false);
    });

    expect(res).toEqual({ success: false, error: 'Microsoft authentication could not be verified' });
  });

  it('Popup cancellation is neutral', async () => {
    // 28
    mockMsal.instance.getActiveAccount.mockReturnValue(null);
    const cancelError: any = new Error('Cancelled');
    cancelError.name = 'BrowserAuthError';
    cancelError.errorCode = 'user_cancelled';
    mockMsal.instance.loginPopup.mockRejectedValue(cancelError);

    const { result } = renderHook(() => useMicrosoftAuth());
    
    let res;
    await act(async () => {
      res = await result.current.login(false);
    });

    expect(res).toEqual({ success: false, error: '' });
    expect(result.current.error).toBeNull();
  });

  it('Backend failure resets loading state and maps errors', async () => {
    // 26, 27, 29, 30
    mockMsal.instance.getActiveAccount.mockReturnValue(null);
    mockMsal.instance.loginPopup.mockResolvedValue({ accessToken: 'token', account: {} });
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: 'account_linking_required' })
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    
    let res;
    await act(async () => {
      res = await result.current.login(false);
    });

    expect(res).toEqual({ success: false, error: 'an existing LogSentinel account must be explicitly linked' });
    expect(result.current.loading).toBe(false);
    expect(setAuthToken).not.toHaveBeenCalled();
  });
});

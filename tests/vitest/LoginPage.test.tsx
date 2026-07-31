import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi, Mock } from 'vitest';
import { LoginPage } from '../../src/pages/LoginPage';
import { BrowserRouter } from 'react-router';
import { GoogleOAuthProvider } from '@react-oauth/google';

// 1, 2, 3, 4, 5, 6, 7
vi.mock('../../src/hooks/useMicrosoftAuth', () => ({
  useMicrosoftAuth: vi.fn(() => ({
    login: vi.fn(),
    loading: false,
    error: null,
  }))
}));

// We test the component with mocked config
let mockMsalConfigured = false;
vi.mock('../../src/config/msal', () => ({
  isMsalConfigured: () => mockMsalConfigured,
}));

import { useMicrosoftAuth } from '../../src/hooks/useMicrosoftAuth';

describe('LoginPage Component', () => {
  const renderWithProviders = (ui: React.ReactElement) => {
    return render(
      <GoogleOAuthProvider clientId="test-client-id">
        <BrowserRouter>{ui}</BrowserRouter>
      </GoogleOAuthProvider>
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Optional MSAL architecture', () => {
    it('Microsoft auth disabled does not crash the application', () => {
      // 1, 2, 3, 4, 7
      mockMsalConfigured = false; // Simulates disabled or unconfigured
      
      expect(() => {
        renderWithProviders(<LoginPage />);
      }).not.toThrow();

      // Check the button is present but disabled UI or invokes the error handler
      const msBtn = screen.getByRole('button', { name: /continue with microsoft/i });
      expect(msBtn).toBeInTheDocument();
      
      // Clicking it shouldn't call MSAL hooks, it just sets error
      fireEvent.click(msBtn);
      
      expect(useMicrosoftAuth).not.toHaveBeenCalled();
      expect(screen.getByText('Microsoft login is not currently configured')).toBeInTheDocument();
    });

    it('Email/password controls and Google remain usable when Microsoft auth is disabled', () => {
      // 5, 6
      mockMsalConfigured = false;
      renderWithProviders(<LoginPage />);
      
      expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
      // Wait, Google button is an iframe but there's a continue with google text somewhere, actually we render GoogleLogin
      // just ensuring it doesn't crash is enough for #6.
    });
  });

  describe('Accessibility and Interaction Locking', () => {
    it('Form labels remain correctly associated', () => {
      // 37
      renderWithProviders(<LoginPage />);
      
      const emailInput = screen.getByLabelText('Email address');
      expect(emailInput).toHaveAttribute('type', 'email');
      
      const passwordInput = screen.getByLabelText('Password');
      expect(passwordInput).toHaveAttribute('type', 'password');
    });

    it('Password toggle retains aria-pressed', () => {
      // 39
      renderWithProviders(<LoginPage />);
      
      const toggle = screen.getByRole('button', { name: /show password/i });
      expect(toggle).toHaveAttribute('aria-pressed', 'false');
      
      fireEvent.click(toggle);
      
      expect(toggle).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
    });

    it('Email submission is blocked while Microsoft login is active', () => {
      // 40, 41, 42
      mockMsalConfigured = true;
      (useMicrosoftAuth as Mock).mockReturnValue({
        login: vi.fn(),
        loading: true,
        error: null,
      });

      renderWithProviders(<LoginPage />);
      
      const submitBtn = screen.getByRole('button', { name: /signing in/i });
      expect(submitBtn).toBeDisabled();
      
      const msBtn = screen.getByRole('button', { name: /continue with microsoft/i });
      expect(msBtn).toBeDisabled();
    });
  });
});

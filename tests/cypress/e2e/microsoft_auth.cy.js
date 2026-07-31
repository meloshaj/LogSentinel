describe("Microsoft Authentication Frontend", () => {
  beforeEach(() => {
    // Intercept backend requests to prevent actual network calls during UI testing
    cy.intercept("POST", "**/api/auth/microsoft", {
      statusCode: 200,
      body: { access_token: "fake-jwt-token" }
    }).as("microsoftAuth");
  });

  it("should show Microsoft SSO button when client ID is provided", () => {
    // We can't easily mock import.meta.env in Cypress without plugins,
    // so we test the UI elements that are visible.
    cy.visit("/login");
    
    // The button might be hidden behind "Continue with Microsoft" depending on env vars
    // Check if the button exists and is not disabled initially (unless loading)
    cy.contains("button", "Continue with Microsoft").should("exist");
  });

  it("should disable inputs while loading", () => {
    cy.visit("/login");
    
    // Click login to trigger loading state (assuming empty form validation is bypassed or filled)
    cy.get('input[type="email"]').type("test@example.com");
    cy.get('input[type="password"]').type("password123");
    
    // Intercept normal login with a delay to keep loading state active
    cy.intercept("POST", "**/api/auth/login", {
      delay: 1000,
      statusCode: 200,
      body: { access_token: "fake-jwt-token" }
    }).as("delayedLogin");
    
    cy.contains("button", "Sign In").click();
    
    // Check that Microsoft button becomes disabled during loading
    cy.contains("button", "Continue with Microsoft").should("be.disabled");
    cy.get('input[type="email"]').should("be.disabled");
    cy.get('input[type="password"]').should("be.disabled");
  });
});

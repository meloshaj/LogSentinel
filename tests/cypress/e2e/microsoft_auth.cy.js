describe("Microsoft authentication login surface", () => {
  it("fails closed without compiled Microsoft configuration", () => {
    cy.visit("/login");

    cy.contains("button", "Continue with Microsoft")
      .should("exist")
      .and("be.disabled")
      .and("have.attr", "aria-describedby", "microsoft-login-availability");
    cy.get("#microsoft-login-availability").should(
      "contain.text",
      "Microsoft sign-in is not configured.",
    );
    cy.get("#email-address").should("be.enabled");
    cy.get("#password").should("be.enabled");
  });

  it("locks email and provider controls during an email request", () => {
    cy.intercept("POST", "**/api/auth/login", {
      delay: 1000,
      statusCode: 401,
      body: { detail: "Invalid credentials" },
    }).as("delayedLogin");
    cy.visit("/login");
    cy.get("#email-address").type("test@example.com");
    cy.get("#password").type("password123");

    cy.contains("button", "Sign In").click();

    cy.get("#email-address").should("be.disabled");
    cy.get("#password").should("be.disabled");
    cy.contains("button", "Continue with Microsoft").should("be.disabled");
    cy.wait("@delayedLogin");
    cy.get("#email-address").should("be.enabled");
    cy.get('[role="alert"]').should("contain.text", "Invalid credentials");
  });
});

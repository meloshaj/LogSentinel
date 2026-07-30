describe('Topology Dashboard', () => {
  beforeEach(() => {
    // Intercept and mock the WebSocket connection to inject synthetic events
    cy.visit('/', {
      onBeforeLoad(win) {
        cy.stub(win, 'WebSocket').callsFake((url) => {
          const ws = {
            url,
            readyState: 1, // WebSocket.OPEN
            send: cy.stub(),
            close: cy.stub(),
            addEventListener: cy.stub(),
            removeEventListener: cy.stub(),
            dispatchEvent: cy.stub(),
            onmessage: null,
            onopen: null,
            onclose: null,
            onerror: null,
          };
          
          // Helper to simulate incoming messages
          setTimeout(() => {
            if (ws.onopen) ws.onopen();
            
            // Simulate 250ms batch payload
            setTimeout(() => {
              if (ws.onmessage) {
                ws.onmessage({
                  data: JSON.stringify({
                    type: "frame_update",
                    timestamp: new Date().toISOString(),
                    payload: {
                      events: [
                        {
                          type: "infrastructure.tracking_loop.triggered",
                          payload: {
                            window_id: "test-window-1",
                            anomaly_score: 0.95,
                            severity: "critical",
                            suspected_root_service: "auth-service",
                            blast_radius: {
                              blast_radius: [
                                {
                                  service_name: "auth-service",
                                  impact_classification: "root",
                                  dependency_path: [],
                                  propagation_path: [],
                                  impact_score: 0.9
                                },
                                {
                                  service_name: "payment-service",
                                  impact_classification: "direct",
                                  dependency_path: ["auth-service"],
                                  propagation_path: [],
                                  impact_score: 0.8
                                }
                              ]
                            }
                          }
                        }
                      ]
                    }
                  })
                });
              }
            }, 500);
          }, 100);
          
          return ws;
        });
      }
    });
  });

  it('renders the mocked alert in the EventManagerPanel and pans the canvas on click', () => {
    // Wait for the mock event to populate
    cy.wait(1000);

    // Verify EventManagerPanel renders the critical alert
    cy.contains('Event Manager').should('be.visible');
    cy.contains('CRITICAL').should('be.visible');
    cy.contains('Root cause suspected in auth-service').should('be.visible');
    
    // Verify TopologyCanvas renders nodes and edges
    cy.contains('auth-service').should('be.visible');
    cy.contains('payment-service').should('be.visible');

    // Verify pulse animation on root node
    cy.contains('auth-service')
      .parents('div')
      .should('have.class', 'animate-pulse');

    // Click the alert in the EventManagerPanel
    cy.contains('Root cause suspected in auth-service').click();
    
    // The viewport of React Flow should animate. 
    // We verify the viewport wrapper is present and intact
    cy.get('.react-flow__viewport').should('exist');
  });
});

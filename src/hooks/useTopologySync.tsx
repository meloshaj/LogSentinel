import React, { createContext, useContext, useState, ReactNode } from 'react';

interface TopologySyncContextType {
  selectedNodeId: string | null;
  setSelectedNodeId: (id: string | null) => void;
}

const TopologySyncContext = createContext<TopologySyncContextType | undefined>(undefined);

export function TopologySyncProvider({ children }: { children: ReactNode }) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  return (
    <TopologySyncContext.Provider value={{ selectedNodeId, setSelectedNodeId }}>
      {children}
    </TopologySyncContext.Provider>
  );
}

export function useTopologySync() {
  const context = useContext(TopologySyncContext);
  if (context === undefined) {
    throw new Error('useTopologySync must be used within a TopologySyncProvider');
  }
  return context;
}

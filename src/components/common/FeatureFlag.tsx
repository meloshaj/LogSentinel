import { ReactNode } from 'react';
import { FEATURE_FLAGS, FeatureFlags } from '../../config/features';

interface FeatureFlagProps {
  flag: keyof FeatureFlags;
  children: ReactNode;
  fallback?: ReactNode;
}

export function FeatureFlag({ flag, children, fallback = null }: FeatureFlagProps) {
  if (FEATURE_FLAGS[flag]) {
    return <>{children}</>;
  }
  
  return <>{fallback}</>;
}

export function useFeatureFlag(flag: keyof FeatureFlags): boolean {
  return FEATURE_FLAGS[flag];
}

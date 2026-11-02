const OPERATOR_TOKEN_KEY = "operator_token";

// Change events let the login gate react without prop drilling: the API
// client clears the stored token on any 401, and the gate re-opens.
export const OPERATOR_AUTH_CHANGED_EVENT = "operator-auth:changed";

export function getOperatorToken(): string {
  return sessionStorage.getItem(OPERATOR_TOKEN_KEY) ?? "";
}

export function hasOperatorToken(): boolean {
  return getOperatorToken().length > 0;
}

export function setOperatorToken(token: string): void {
  sessionStorage.setItem(OPERATOR_TOKEN_KEY, token.trim());
  window.dispatchEvent(new Event(OPERATOR_AUTH_CHANGED_EVENT));
}

export function clearOperatorToken(): void {
  sessionStorage.removeItem(OPERATOR_TOKEN_KEY);
  window.dispatchEvent(new Event(OPERATOR_AUTH_CHANGED_EVENT));
}

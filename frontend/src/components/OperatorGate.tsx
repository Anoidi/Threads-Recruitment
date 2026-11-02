import { useEffect, useRef, useState } from "react";
import { Button } from "./ui";
import {
  OPERATOR_AUTH_CHANGED_EVENT,
  getOperatorToken,
  hasOperatorToken,
  setOperatorToken,
} from "../api/auth";
import { isMockMode } from "../api/client";

function OperatorLogin({ submitted, rejected }: { submitted: boolean; rejected: boolean }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) setOperatorToken(value);
        }}
        className="panel w-full max-w-sm p-6"
      >
        <h1 className="text-lg font-semibold text-slate-800">Вход в панель</h1>
        <p className="mt-2 text-sm text-slate-500">
          Введите операторский токен (<code className="text-xs">OPERATOR_TOKEN</code> из .env сервера). Он хранится
          только в этой вкладке и уходит в заголовке X-Operator-Token.
        </p>
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Операторский токен"
          autoFocus
          className="mt-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-moss-600"
        />
        <Button type="submit" variant="primary" loading={submitted} className="mt-3 w-full">
          Войти
        </Button>
        {rejected && <p className="mt-3 text-sm text-red-700">Токен отклонён сервером. Проверьте значение и повторите.</p>}
      </form>
    </div>
  );
}

export function OperatorGate({ children, forceLive = false }: { children: React.ReactNode; forceLive?: boolean }) {
  // Mock/test builds talk to the bundled mock API and never need a token;
  // ``forceLive`` lets tests render the gate's real behaviour.
  const [tokenPresent, setTokenPresent] = useState(() => (isMockMode && !forceLive) || hasOperatorToken());
  const [submitted, setSubmitted] = useState(false);
  const hadToken = useRef(tokenPresent);
  const [rejected, setRejected] = useState(false);

  useEffect(() => {
    const update = () => {
      const next = (isMockMode && !forceLive) || hasOperatorToken();
      setTokenPresent(next);
      if (!next && hadToken.current) setRejected(true);
      hadToken.current = next;
      setSubmitted(false);
    };
    window.addEventListener(OPERATOR_AUTH_CHANGED_EVENT, update);
    return () => window.removeEventListener(OPERATOR_AUTH_CHANGED_EVENT, update);
  }, [forceLive]);

  useEffect(() => {
    if (!tokenPresent) {
      // A fresh tab may already hold a stale (cleared) token: the 401 path
      // clears it again, but the login screen starts clean either way.
      setRejected(hadToken.current);
      setSubmitted(false);
    }
  }, [tokenPresent]);

  if ((isMockMode && !forceLive) || tokenPresent) return children;
  return <OperatorLogin submitted={submitted} rejected={rejected} />;
}

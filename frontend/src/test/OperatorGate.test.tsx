import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { OperatorGate } from "../components/OperatorGate";
import { clearOperatorToken, getOperatorToken, setOperatorToken } from "../api/auth";

afterEach(() => clearOperatorToken());

describe("operator token storage", () => {
  it("stores the token in sessionStorage only", () => {
    expect(getOperatorToken()).toBe("");
    setOperatorToken("token-1");
    expect(getOperatorToken()).toBe("token-1");
    clearOperatorToken();
    expect(getOperatorToken()).toBe("");
  });
});

describe("operator gate (live mode)", () => {
  it("asks for the token when the session has none", () => {
    render(<OperatorGate forceLive><div>Работа панели</div></OperatorGate>);
    expect(screen.getByText("Вход в панель")).toBeInTheDocument();
    expect(screen.queryByText("Работа панели")).not.toBeInTheDocument();
  });

  it("admits the operator after a token is stored", async () => {
    setOperatorToken("token-1");
    render(<OperatorGate forceLive><div>Работа панели</div></OperatorGate>);
    expect(await screen.findByText("Работа панели")).toBeInTheDocument();
    expect(screen.queryByText("Вход в панель")).not.toBeInTheDocument();
  });

  it("submits the entered token and unlocks children", async () => {
    const user = userEvent.setup();
    render(<OperatorGate forceLive><div>Работа панели</div></OperatorGate>);
    await user.type(screen.getByPlaceholderText("Операторский токен"), "token-2");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText("Работа панели")).toBeInTheDocument();
    expect(getOperatorToken()).toBe("token-2");
  });
});

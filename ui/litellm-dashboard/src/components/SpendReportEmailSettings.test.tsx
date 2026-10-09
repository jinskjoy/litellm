import { renderWithProviders, screen, waitFor } from "../../tests/test-utils";
import { vi } from "vitest";
import { SpendReportEmailSettings } from "./SpendReportEmailSettings";
import * as networking from "./networking";

vi.mock("./networking", () => ({
  getSpendReportEmailSettings: vi.fn(),
  updateSpendReportEmailSettings: vi.fn(),
  sendSpendReportEmailTest: vi.fn(),
}));

describe("SpendReportEmailSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders card title and description", async () => {
    vi.mocked(networking.getSpendReportEmailSettings).mockResolvedValue({
      values: {
        enabled: true,
        frequency: "both",
        daily_send_time: "08:30",
        monthly_send_time: "10:00",
        recipient_emails: ["finance@example.com"],
        group_by: ["team", "team_key_model"],
      },
      field_descriptions: {},
    });

    renderWithProviders(<SpendReportEmailSettings accessToken="test-token" />);

    expect(screen.getByText("Scheduled Spend Reports (Email / SMTP)")).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByText(
          "Automatically send daily and/or monthly spend reports to configured email addresses. Includes cumulative breakdowns by team, API keys, and models.",
        ),
      ).toBeInTheDocument();
      expect(screen.getByDisplayValue("finance@example.com")).toBeInTheDocument();
    });
  });

  it("renders disabled state badge when enabled is false", async () => {
    vi.mocked(networking.getSpendReportEmailSettings).mockResolvedValue({
      values: {
        enabled: false,
        frequency: "daily",
        daily_send_time: "09:00",
        monthly_send_time: "09:00",
        recipient_emails: [],
        group_by: ["team"],
      },
      field_descriptions: {},
    });

    renderWithProviders(<SpendReportEmailSettings accessToken="test-token" />);

    await waitFor(() => {
      const disabledElements = screen.getAllByText("Disabled");
      expect(disabledElements.length).toBeGreaterThan(0);
    });
  });
});

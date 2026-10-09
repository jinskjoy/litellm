import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { toast } from "@/lib/toast";
import {
  getSpendReportEmailSettings,
  updateSpendReportEmailSettings,
  sendSpendReportEmailTest,
  type SpendReportEmailSettingsValues,
} from "./networking";

interface SpendReportEmailSettingsProps {
  accessToken: string | null;
}

type GroupByOptionId = "team" | "team_key" | "team_model" | "team_key_model";

interface GroupByOption {
  readonly id: GroupByOptionId;
  readonly label: string;
  readonly description: string;
}

const GROUP_BY_OPTIONS: readonly GroupByOption[] = [
  { id: "team", label: "Team", description: "Aggregates spend grouped by team" },
  { id: "team_key", label: "Team + Keys", description: "Aggregates spend grouped by team and API key" },
  { id: "team_model", label: "Team + Models", description: "Aggregates spend grouped by team and model" },
  { id: "team_key_model", label: "Team + Key + Models", description: "Aggregates spend grouped by team, key, and model" },
];

const DEFAULT_SETTINGS: SpendReportEmailSettingsValues = {
  enabled: false,
  frequency: "daily",
  daily_send_time: "09:00",
  monthly_send_time: "09:00",
  recipient_emails: [],
  group_by: ["team"],
};

export const SpendReportEmailSettings: React.FC<SpendReportEmailSettingsProps> = ({ accessToken }) => {
  const [loading, setLoading] = useState<boolean>(Boolean(accessToken));
  const [saving, setSaving] = useState(false);
  const [sendingTest, setSendingTest] = useState(false);

  const [settings, setSettings] = useState<SpendReportEmailSettingsValues>(DEFAULT_SETTINGS);

  const [recipientsInput, setRecipientsInput] = useState("");

  useEffect(() => {
    let active = true;
    if (!accessToken) {
      return;
    }

    getSpendReportEmailSettings(accessToken)
      .then((response) => {
        if (active && response?.values) {
          setSettings(response.values);
          setRecipientsInput((response.values.recipient_emails || []).join(", "));
        }
      })
      .catch((error) => {
        if (active) {
          console.error("Failed to load spend report email settings:", error);
          toast.fromError("Failed to load spend report email settings");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [accessToken]);

  const handleGroupByToggle = (optionId: GroupByOptionId, checked: boolean) => {
    setSettings((prev) => {
      let updated: GroupByOptionId[];
      if (checked) {
        updated = [...prev.group_by, optionId];
      } else {
        updated = prev.group_by.filter((g) => g !== optionId);
      }
      if (updated.length === 0) {
        updated = ["team"];
      }
      return { ...prev, group_by: updated };
    });
  };

  const handleSave = async () => {
    if (!accessToken) return;

    const parsedRecipients = recipientsInput
      .split(",")
      .map((e) => e.trim())
      .filter((e) => e.length > 0);

    const payload: SpendReportEmailSettingsValues = {
      ...settings,
      recipient_emails: parsedRecipients,
    };

    setSaving(true);
    try {
      await updateSpendReportEmailSettings(accessToken, payload);
      setSettings(payload);
      toast.success("Spend report email settings saved successfully");
    } catch (error) {
      console.error("Failed to save spend report email settings:", error);
      toast.fromError("Failed to save spend report email settings");
    } finally {
      setSaving(false);
    }
  };

  const handleSendTestReport = async () => {
    if (!accessToken) return;

    const parsedRecipients = recipientsInput
      .split(",")
      .map((e) => e.trim())
      .filter((e) => e.length > 0);

    if (parsedRecipients.length === 0) {
      toast.error("Please provide at least one recipient email address to send a test report");
      return;
    }

    setSendingTest(true);
    try {
      const result = await sendSpendReportEmailTest(accessToken, {
        frequency: settings.frequency === "monthly" ? "monthly" : "daily",
        recipient_emails: parsedRecipients,
        group_by: settings.group_by,
      });
      toast.success(
        `Test spend report email sent! Total spend: $${result.total_spend ?? 0} across ${result.total_requests ?? 0} request(s).`,
      );
    } catch (error) {
      console.error("Failed to send test spend report email:", error);
      toast.fromError(error);
    } finally {
      setSendingTest(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base font-semibold">Scheduled Spend Reports (Email / SMTP)</CardTitle>
              <Badge variant={settings.enabled ? "default" : "outline"}>
                {settings.enabled ? "Active" : "Disabled"}
              </Badge>
            </div>
            <CardDescription className="text-sm text-muted-foreground">
              Automatically send daily and/or monthly spend reports to configured email addresses. Includes cumulative
              breakdowns by team, API keys, and models.
            </CardDescription>
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-sm font-medium">{settings.enabled ? "Enabled" : "Disabled"}</span>
            <Switch
              checked={settings.enabled}
              onCheckedChange={(checked) => setSettings((prev) => ({ ...prev, enabled: checked }))}
              disabled={loading || saving}
            />
          </div>
        </div>
      </CardHeader>

      <CardContent>
        <Separator className="mb-6" />

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Report Frequency</label>
                <Select
                  value={settings.frequency}
                  onValueChange={(val: "daily" | "monthly" | "both" | null) => {
                    if (val) {
                      setSettings((prev) => ({ ...prev, frequency: val }));
                    }
                  }}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select frequency" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="daily">Daily Report (covers previous day)</SelectItem>
                    <SelectItem value="monthly">Monthly Report (1st of month for previous month)</SelectItem>
                    <SelectItem value="both">Both Daily and Monthly Reports</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Choose whether to send spend reports daily, on the 1st of each month, or both.
                </p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Recipient Email Addresses</label>
                <Input
                  placeholder="e.g. finops@example.com, manager@example.com"
                  value={recipientsInput}
                  onChange={(e) => setRecipientsInput(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Comma-separated email addresses that will receive the spend reports.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {(settings.frequency === "daily" || settings.frequency === "both") && (
                <div className="space-y-2">
                  <label className="text-sm font-medium text-foreground">Daily Send Time (UTC)</label>
                  <Input
                    placeholder="09:00"
                    value={settings.daily_send_time}
                    onChange={(e) => setSettings((prev) => ({ ...prev, daily_send_time: e.target.value }))}
                  />
                  <p className="text-xs text-muted-foreground">
                    Time in 24-hour UTC format (HH:MM) when the daily report will be delivered.
                  </p>
                </div>
              )}

              {(settings.frequency === "monthly" || settings.frequency === "both") && (
                <div className="space-y-2">
                  <label className="text-sm font-medium text-foreground">Monthly Send Time (UTC)</label>
                  <Input
                    placeholder="09:00"
                    value={settings.monthly_send_time}
                    onChange={(e) => setSettings((prev) => ({ ...prev, monthly_send_time: e.target.value }))}
                  />
                  <p className="text-xs text-muted-foreground">
                    Time in 24-hour UTC format (HH:MM) on the 1st of every month to send previous month&apos;s report.
                  </p>
                </div>
              )}
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium text-foreground">Cumulative Report Groupings</label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Select which breakdown tables to include in the email report. Total cost is rounded to 2 decimal places
                  and only rows with spend greater than 0 are included.
                </p>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                {GROUP_BY_OPTIONS.map((opt) => {
                  const isChecked = settings.group_by.includes(opt.id);
                  return (
                    <div
                      key={opt.id}
                      className="flex items-start space-x-3 p-3 rounded-md border border-border bg-muted/40 hover:bg-muted/60 transition-colors"
                    >
                      <Checkbox
                        id={`groupby-${opt.id}`}
                        checked={isChecked}
                        onCheckedChange={(checked) =>
                          handleGroupByToggle(opt.id, Boolean(checked))
                        }
                        className="mt-0.5"
                      />
                      <label htmlFor={`groupby-${opt.id}`} className="cursor-pointer space-y-0.5 select-none">
                        <div className="text-sm font-medium text-foreground">{opt.label}</div>
                        <div className="text-xs text-muted-foreground">{opt.description}</div>
                      </label>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3 pt-4 border-t border-border">
              <Button onClick={handleSave} disabled={saving || loading}>
                {saving ? "Saving..." : "Save Spend Report Settings"}
              </Button>
              <Button
                variant="outline"
                onClick={handleSendTestReport}
                disabled={sendingTest || saving || loading}
              >
                {sendingTest ? "Sending Test Report..." : "Send Test Report Now"}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default SpendReportEmailSettings;

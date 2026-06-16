import { Router } from "express";
import { z } from "zod";
import { egxAiEmbeddedService } from "./services/egxAiEmbeddedService.js";

const symbolSchema = z.string().regex(/^[A-Z0-9]{2,12}$/);
const boolParam = z.preprocess((value) => {
  if (typeof value === "string") return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
  return Boolean(value);
}, z.boolean());
const querySchema = z.object({
  sectorFilter: z.string().optional().default(""),
  nameFilter: z.string().optional().default(""),
  page: z.coerce.number().int().min(0).default(0),
  size: z.coerce.number().int().min(1).max(500).default(10),
  periodParam: z.string().optional().default("1 day"),
  intervalParam: z.string().optional().default("1 years"),
  forceRefresh: boolParam.optional().default(false),
});

export function createEgxAiCompatibleRoutes() {
  const router = Router();

  router.get("/stocks", async (req, res) => {
    const query = querySchema.safeParse(req.query);
    if (!query.success) return res.status(400).json({ error: "Invalid stock query" });
    const result = await egxAiEmbeddedService.getStocks(query.data.sectorFilter, query.data.nameFilter, query.data.page, query.data.size, { forceRefresh: query.data.forceRefresh });
    res.json(result);
  });

  router.get("/stocks/:reutersCode", async (req, res) => {
    const symbol = symbolSchema.safeParse(req.params.reutersCode.toUpperCase());
    if (!symbol.success) return res.status(400).json({ error: "Invalid Reuters code" });
    try {
      const query = querySchema.safeParse(req.query);
      res.json(await egxAiEmbeddedService.getStock(symbol.data, { forceRefresh: query.success ? query.data.forceRefresh : false }));
    } catch (error) {
      res.status(404).json({ error: error instanceof Error ? error.message : "Stock/Equity not found" });
    }
  });

  router.get("/historical-stocks/:reutersCode", async (req, res) => {
    const symbol = symbolSchema.safeParse(req.params.reutersCode.toUpperCase());
    const query = querySchema.safeParse(req.query);
    if (!symbol.success || !query.success) return res.status(400).json({ error: "Invalid historical stock query" });
    res.json(await egxAiEmbeddedService.getHistorical(symbol.data, query.data.page, query.data.size, { forceRefresh: query.data.forceRefresh }));
  });

  return router;
}

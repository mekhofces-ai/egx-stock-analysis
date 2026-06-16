import { PrismaClient } from "@prisma/client";
import { egxStocks, egxUniverseSource } from "../src/data/egxUniverse.ts";

const prisma = new PrismaClient();

async function main() {
  for (const stock of egxStocks) {
    await prisma.egxSymbol.upsert({
      where: { symbolCode: stock.symbol },
      update: {
        tradingviewSymbol: `EGX:${stock.symbol}`,
        companyNameEn: stock.companyName,
        sector: stock.sector,
        isActive: stock.isActive,
        isPlaceholder: false,
      },
      create: {
        symbolCode: stock.symbol,
        tradingviewSymbol: `EGX:${stock.symbol}`,
        companyNameEn: stock.companyName,
        companyNameAr: null,
        sector: stock.sector,
        industry: null,
        isActive: stock.isActive,
        isPlaceholder: false,
      },
    });
  }

  await prisma.providerStatus.upsert({
    where: { provider: "symbol-universe" },
    update: { status: "available", reason: `${egxUniverseSource.count} active EGX symbols seeded from ${egxUniverseSource.provider}.`, checkedAt: new Date() },
    create: { provider: "symbol-universe", status: "available", reason: `${egxUniverseSource.count} active EGX symbols seeded from ${egxUniverseSource.provider}.` },
  });
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => prisma.$disconnect());

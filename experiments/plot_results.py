import matplotlib.pyplot as plt
import numpy as np

# Configure figure styles
plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

# Graph 1: Median Latency Under Varying Conditions (Logarithmic Scale)
conditions = ["Baseline (0% Loss)", "10% Packet Loss", "50ms Injected Delay"]
tcp_latencies = [0.077, 322.149, 50.069]
udp_latencies = [0.097, 0.101, 50.082]

x = np.arange(len(conditions))
width = 0.35

rects1 = ax1.bar(x - width/2, tcp_latencies, width, label="TCP (Stream)", color="#1b4931")
rects2 = ax1.bar(x + width/2, udp_latencies, width, label="UDP (Datagram)", color="#d97706")

ax1.set_ylabel("Median Latency (ms) [Log Scale]", fontsize=11, fontweight="bold")
ax1.set_title("Median Transport Latency Comparison", fontsize=12, fontweight="bold")
ax1.set_xticks(x)
ax1.set_xticklabels(conditions, fontsize=9)
ax1.set_yscale("log")
ax1.legend(frameon=True)
ax1.grid(True, which="both", ls="--", alpha=0.5)

for rect in rects1:
    h = rect.get_height()
    ax1.annotate(f"{h:.2f}ms", xy=(rect.get_x() + rect.get_width()/2, h),
                 xytext=(0, 4), textcoords="offset points", ha="center", va="bottom", fontsize=8)
for rect in rects2:
    h = rect.get_height()
    ax1.annotate(f"{h:.2f}ms", xy=(rect.get_x() + rect.get_width()/2, h),
                 xytext=(0, 4), textcoords="offset points", ha="center", va="bottom", fontsize=8)

# Graph 2: Delivery Reliability Under 10% Channel Loss
protocols = ["TCP", "UDP"]
delivered = [100, 86]
lost = [0, 14]

ax2.bar(protocols, delivered, label="Delivered Packets", color="#2e7d32", width=0.45)
ax2.bar(protocols, lost, bottom=delivered, label="Lost / Dropped Packets", color="#dc2626", width=0.45)

ax2.set_ylabel("Packet Count (out of 100)", fontsize=11, fontweight="bold")
ax2.set_title("Reliability Under 10% Channel Impairment", fontsize=12, fontweight="bold")
ax2.set_ylim(0, 115)
ax2.legend(frameon=True, loc="upper right")

for i, (d, l) in enumerate(zip(delivered, lost)):
    ax2.text(i, d / 2, f"{d}% Delivered", ha="center", va="center", color="white", fontweight="bold", fontsize=10)
    if l > 0:
        ax2.text(i, d + (l / 2), f"{l}% Lost", ha="center", va="center", color="white", fontweight="bold", fontsize=10)

plt.tight_layout()
plt.savefig("benchmark_comparison.png", dpi=300)
print("[*] Saved benchmark_comparison.png successfully.")
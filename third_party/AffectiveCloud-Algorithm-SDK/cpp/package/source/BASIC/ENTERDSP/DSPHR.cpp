#include "DSPHR.h"
#include "Basic.hpp"
#include "FFT.hpp"
#include <cmath>
#include <algorithm>

namespace basic
{
    namespace dsp
    {
        vectord hrIntervalCal(const vectord &hrSeq)
        {
            vectord hrIntervalSeq;
            for (size_t i = 0; i < hrSeq.size(); i++)
            {
                if (hrSeq[i] > 0)
                    hrIntervalSeq.push_back(60000 / hrSeq[i]);
                else
                    hrIntervalSeq.push_back(0);
            }
            return hrIntervalSeq;
        }

        double hrvValCal(const vectord &hrIntervalSeq, double seqLim, DeviceInfo *deviceInfo)
        {
            double hrvVal = 0;
            auto hrFs = deviceInfo->hrFs();
            if (hrIntervalSeq.size() < size_t(seqLim * hrFs))
            {
                hrvVal = 0;
            }
            else
            {
                vectord split;
                for (size_t i = 0; i < hrIntervalSeq.size(); i += int(hrFs))
                {
                    split.push_back(hrIntervalSeq[i]);
                }

                hrvVal = mathtool::stdv(split, 1);
                hrvVal = std::min(hrvVal, 255.0);
            }
            return hrvVal;
        }

        vectord hrSeqCal(const vectord &hrSeq, double seqLim, DeviceInfo *deviceInfo)
        {
            auto hrFs = int(deviceInfo->hrFs() * seqLim);

            if (hrSeq.size() > hrFs)
            {
                vectord hrIntervalSeq;

                for (size_t i = 0; i < hrSeq.size(); i++)
                {
                    hrIntervalSeq.push_back(60000 / hrSeq[i]);
                }

                auto len = hrSeq.size() - hrFs + 1;
                vectord hrvSeq(len);
                for (size_t i = 0; i < len; i++)
                {
                    auto split = mathtool::truncate(hrIntervalSeq, i, i + hrFs);
                    hrvSeq[i] = mathtool::stdv(split, 1);
                }
                return hrvSeq;
            }
            else
            {
                vectord hrvSeq;
                return hrvSeq;
            }
        }

        double hrvPowerCal(const vectord &hrvSeq, double freqLowLim, double freqHighLim, double fs)
        {
            if (freqLowLim > freqHighLim)
                throw std::invalid_argument("The lower cut-off frequency is higher than the upper cut-off frequency!");
            if (freqHighLim > fs)
                throw std::invalid_argument("The upper cut-off frequency cannot be higher than the sample rate!");
            
            auto nfft = hrvSeq.size();

            auto fft = mathtool::fft(hrvSeq, nfft);

            auto hrvSpec = mathtool::abs(fft);

            auto hrvSpecTemp = mathtool::truncate(hrvSpec, 0, static_cast<int>(round(0.4/fs*nfft)));

            auto hrvSpecTemp2 = mathtool::truncate(hrvSpecTemp, static_cast<int>(round(freqLowLim/fs*nfft)), static_cast<int>(round(freqHighLim/fs*nfft)));

            auto bandPower = mathtool::sum(hrvSpecTemp2);
            return bandPower;
        }

        /// 有效性校验
        /// \param nnIntervalData 心率间期数据
        /// \return
        std::tuple<double, double, double> hrStatMetricsCal(const std::vector<double>& nnIntervalData) {
            // 有效性校验
            if (nnIntervalData.size() < 100)
                return std::make_tuple(0.0, 0.0, 0.0);
            // 分布统计
            std::vector<int> nnIntervalDistribution(32, 0); //心率间期分布（直方统计，间隔50ms，统计范围为400~2000ms）
            for (const auto& d : nnIntervalData) {
                for (int i = 0; i < 32; i++) {
                    if (400 + 50 * i <= d && d <= 400 + 50 * (i + 1)) {
                        nnIntervalDistribution[i] += 1;
                        break;
                    }
                }
            }

            // 指标计算
            auto maxElement = std::max_element(nnIntervalDistribution.begin(), nnIntervalDistribution.end());
            double amo = static_cast<double>(*maxElement) / nnIntervalData.size(); //幅度
            double mo = (std::distance(nnIntervalDistribution.begin(), maxElement) * 50 + 425) / 1000.0; //众数
            double mxdmn = std::ceil(((*std::max_element(nnIntervalData.begin(), nnIntervalData.end())) -
                    (*std::min_element(nnIntervalData.begin(), nnIntervalData.end()))) / 50) * 50 / 1000.0; // 跨度
            return std::make_tuple(amo, mo, mxdmn);
        }
    } // namespace dsp

} // namespace basic

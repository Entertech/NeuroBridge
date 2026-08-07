#include "DSPFrac.h"
#include "Basic.hpp"
#include "NumCpp.hpp"
namespace basic
{
    namespace dsp
    {
        /// @brief 分段包络计算
        /// @param wave 波形
        /// @param fracNum 分段数量
        /// @return 下包络，上包络
        std::pair<vectord, vectord> fracEnvelopeCal(const vectord &wave, size_t fracNum)
        {
            auto waveLen = wave.size();
            auto fracLen = waveLen / fracNum;
            vectord lowerEnvelope;
            vectord upperEnvelope;
            // vectori lowerPoint;
            // vectori upperPoint;
            size_t i = 0;

            for (size_t i = 0; i * fracLen < waveLen; i++)
            {
                vectord fracWave;
                for (size_t j = i * fracLen; j < (i + 1) * fracLen; j++)
                {
                    if (j < wave.size())
                    {
                        fracWave.push_back(wave.at(j));
                    }


                }
                lowerEnvelope.push_back(mathtool::min(fracWave));
                upperEnvelope.push_back(mathtool::max(fracWave));
                // auto lowerValue = mathtool::argmin(fracWave) + i * fracLen;
                // lowerPoint.push_back(lowerValue);
                // auto upperValue = mathtool::argmax(fracWave) + i * fracLen;
                // upperPoint.push_back(upperValue);
            }
            return std::make_pair(lowerEnvelope, upperEnvelope);
        }

        /// @brief 分段能量计算
        /// @param wave 波形
        /// @param fracNum 分段数量
        /// @return 
        vectord fracPowerCal(const vectord &wave, size_t fracNum)
        {
            auto waveLen = wave.size();
            auto fracLen = waveLen / fracNum;
            vectord power;
            for (size_t i = 0; i * fracLen < waveLen; i++)
            {
                vectord fracWave;
                for (size_t j = i * fracLen; j < (i + 1) * fracLen; j++)
                {
                    if (j < wave.size())
                        fracWave.push_back(wave.at(j));
                }
                auto fracWaveNc = nc::NdArray<double>(fracWave);
                auto powerValue = nc::power(fracWaveNc, 2);
                auto powerSum = nc::sum(powerValue).item();
                power.push_back(powerSum);
            }
            return power;
        }
    }
}
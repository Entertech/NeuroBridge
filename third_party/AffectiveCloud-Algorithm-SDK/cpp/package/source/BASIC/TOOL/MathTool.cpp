#include "NumCpp.hpp"
#include "Basic.hpp"
#include "MathTool.h"
#include <vector>
namespace basic
{
    namespace mathtool
    {

        int mod(int value1, int value2)
        {
            return value1 % value2;
        }

        /**
         * @brief 计算实时均值（利用实时的新数据更新数据缓存，并根据数据缓存计算出一个均值，可用于实时处理变化较剧烈的数据，使其更平稳）
         *
         * @param newData 新数据（可以是一个值或者一个数组）
         * @param dataBuffer 数据缓存
         * @param bufferLim 缓存设定长度（长度越大，初始化过程越长，结果更平稳）
         * @param initialVal 初始值（缓存长度不足时返回的初始化状态值）
         * @return double 实时均值，更新后的数据缓存（达到设定长度后，更新后的缓存与输入的缓存等长）
         */
        double immediateMeanCal(vectord &newData, vectord &dataBuffer, int bufferLim, double initialVal)
        {
            auto bufferLen = dataBuffer.size();
            auto meanVal = initialVal;
            auto ncDataBuffer = nc::NdArray(dataBuffer);
            auto ncNewData = nc::NdArray(newData);

            auto ncDataBufferHStack = nc::hstack({ncDataBuffer, ncNewData});
            auto dataBufferTemp = ncDataBufferHStack.toStlVector();
            dataBuffer.clear();
            dataBuffer.assign(dataBufferTemp.begin(), dataBufferTemp.end());
            if (bufferLen < static_cast<size_t>(bufferLim))
            {
                meanVal = initialVal;
            }
            else
            {
                dataBuffer.insert(dataBuffer.end(), newData.begin(), newData.end());
                dataBuffer.assign(dataBuffer.begin() + newData.size(), dataBuffer.end());
                meanVal = mean(dataBuffer);
            }
            return meanVal;
        };

        /**
         * @brief 硬阈值处理（可用于小波阈值去噪）
         *
         * @param inputData 小波单层数据
         * @param thr 阈值
         */
        void hardThreshold(vectord &inputData, int thr)
        {
            for (std::size_t i = 0; i < inputData.size(); ++i)
            {
                if (inputData[i] > thr)
                {
                    inputData[i] = thr;
                }
                else if (inputData[i] < -thr)
                {
                    inputData[i] = -thr;
                }
            }
        }

        /**
         * @brief 软阈值处理（可用于小波阈值去噪）
         *
         * @param inputData 小波单层数据
         * @param thr 阈值
         * @param order 阶数
         */
        void softThreshold(vectord &inputData, int thr, int order)
        {
            if (thr == NAN)
            {
                auto thrValue = stdv(inputData, 1) * sqrt(2 * std::log(inputData.size()));
                thr = thrValue;
            }
            if (order < 0 || mod(order, 2) == 0)
            {
                throw std::invalid_argument("The order of the soft threshold should be odd!");
            }
            for (std::size_t i = 0; i < inputData.size(); ++i)
            {
                if (std::abs(inputData[i]) > thr)
                {
                    inputData[i] = std::pow(thr, order + 1) / std::pow(inputData[i], order);
                }
            }
        }

        /**
         * @brief 滑动平均
         *
         * @param newData 新数据
         * @param preSmoothValue 上一个滑动平均值
         * @param beta 滑动平均参数
         * @return double
         */
        double smoothAvg(double newData, double preSmoothValue, double beta)
        {
            return (1 - beta) * newData + beta * preSmoothValue;
        }

        /**
         * @brief 脑电能量滑动平均
         *
         * @param newPower 新的脑电能量
         * @param preSmoothPower 上一滑动平均脑电能量
         * @param beta 滑动平均参数
         * @return dsp::EEGPower 滑动平均后的脑电能量
         */
        dsp::EEGPower eegPowerSmoothAvg(dsp::EEGPower newPower, dsp::EEGPower preSmoothPower, double beta)
        {
            dsp::EEGPower eegPower;
            if (newPower.power != 0 && newPower.alpha != 0 && newPower.beta != 0 &&
                newPower.theta != 0 && newPower.delta != 0 && newPower.gamma != 0)
            {
                eegPower.power = smoothAvg(newPower.power, preSmoothPower.power, beta);
                eegPower.alpha = smoothAvg(newPower.alpha, preSmoothPower.alpha, beta);
                eegPower.beta = smoothAvg(newPower.beta, preSmoothPower.beta, beta);
                eegPower.theta = smoothAvg(newPower.theta, preSmoothPower.theta, beta);
                eegPower.delta = smoothAvg(newPower.delta, preSmoothPower.delta, beta);
                eegPower.gamma = smoothAvg(newPower.gamma, preSmoothPower.gamma, beta);
                eegPower.lowBeta = smoothAvg(newPower.lowBeta, preSmoothPower.lowBeta, beta);
                eegPower.highBeta = smoothAvg(newPower.highBeta, preSmoothPower.highBeta, beta);
            }

            return eegPower;
        }

        /**脑电能量调整：耳后脑电->前额脑电
         * return 模型处理后的脑电能量
         */
        dsp::EEGPower eegPowerAdjust(dsp::EEGPower eegPower)
        {
            double waveSum = eegPower.alpha + eegPower.beta + eegPower.theta + eegPower.delta + eegPower.gamma;
            eegPower.gamma += waveSum * 0.025;
            eegPower.beta += waveSum * 0.008;
            eegPower.alpha += waveSum * -0.008;
            eegPower.theta += waveSum * -0.01;
            eegPower.delta += waveSum * -0.015;
            return eegPower;
        }

        /**
         * @brief 脑电能量均值计算
         *
         * @param powerList 脑电能量列表
         * @return dsp::EEGPower 脑电能量均值
         */
        dsp::EEGPower eegMeanPowerCal(const std::vector<dsp::EEGPower> &powerList)
        {
            dsp::EEGPower eegPower;
            vectord eegPowerStore;
            vectord eegAlphaStore;
            vectord eegBetaStore;
            vectord eegThetaStore;
            vectord eegDeltaStore;
            vectord eegGammaStore;
            vectord eegLowBetaStore;
            vectord eegHighBetaStore;

            for (auto &e : powerList)
            {
                if (e.power != 0 && e.alpha != 0 && e.beta != 0 &&
                    e.theta != 0 && e.delta != 0 && e.gamma != 0)
                {
                    eegPowerStore.push_back(e.power);
                    eegAlphaStore.push_back(e.alpha);
                    eegBetaStore.push_back(e.beta);
                    eegThetaStore.push_back(e.theta);
                    eegDeltaStore.push_back(e.delta);
                    eegGammaStore.push_back(e.gamma);
                    eegLowBetaStore.push_back(e.lowBeta);
                    eegHighBetaStore.push_back(e.highBeta);
                }
                else
                    return eegPower;
            }
            nc::NdArray<double> power(eegPowerStore);
            nc::NdArray<double> powerAlpha(eegAlphaStore);
            nc::NdArray<double> powerBeta(eegBetaStore);
            nc::NdArray<double> powerTheta(eegThetaStore);
            nc::NdArray<double> powerDelta(eegDeltaStore);
            nc::NdArray<double> powerGamma(eegGammaStore);
            nc::NdArray<double> powerLowBeta(eegLowBetaStore);
            nc::NdArray<double> powerHighBeta(eegHighBetaStore);
            eegPower.power = nc::mean(power).item();
            eegPower.alpha = nc::mean(powerAlpha).item();
            eegPower.beta = nc::mean(powerBeta).item();
            eegPower.theta = nc::mean(powerTheta).item();
            eegPower.delta = nc::mean(powerDelta).item();
            eegPower.gamma = nc::mean(powerGamma).item();
            eegPower.lowBeta = nc::mean(powerLowBeta).item();
            eegPower.highBeta = nc::mean(powerHighBeta).item();

            return eegPower;
        }

        vectord valueNormalize(const vectord &val, double maxVal, double minVal, const std::string &normMode)
        {
            vectord normVal;
            if (maxVal - minVal > 0)
            {
                if (normMode == "com")
                    for (auto &e : val)
                    {
                        normVal.push_back((e - minVal) / (maxVal - minVal));
                    }
                else if (normMode == "sym")
                    for (auto &e : val)
                    {
                        normVal.push_back(2 * (e - minVal) / (maxVal - minVal) - 1);
                    }
                else
                    throw std::invalid_argument("Undefined norm mode!");
            }
            return normVal;
        }

        nc::NdArray<double> featureNorm(const nc::NdArray<double> &data, nc::NdArray<double> &normParams, const std::string &normMode)
        {
            if (normParams.size() < 2)
                normParams = nc::vstack({nc::max(data, nc::Axis::ROW), nc::min(data, nc::Axis::ROW)}).transpose();
            auto featureNum = data.shape().cols;
            auto normData = nc::zeros_like<double>(data);
            for (size_t i = 0; i < featureNum; i++)
            {
                vectord vecVal = {data.at(0, i)};
                auto maxVal = normParams.at(0, i);
                auto minVal = normParams.at(1, i);
                auto colData = valueNormalize(vecVal, maxVal, minVal, normMode);
                nc::NdArray<double> colDataNc(colData, false);
                normData.at(0, i) = colDataNc.item();
            }
            return normData;
        }

        vectord smoothCurveCal(vectord &rec, int halfSmoothLen)
        {
            auto dataLen = rec.size();
            nc::NdArray<double> recNc(rec, true);
            //首端无效点处理
            for (size_t i = 0; i < dataLen; i++)
            {
                if (recNc.at(i) > 0)
                {
                    if (i>0)
                    {
                        for (size_t j = 0; j < i; j++)
                        {
                            recNc[j] = recNc[i]; 
                        }
                    }
                        // recNc[nc::Slice(i)] = recNc.at(i)*nc::ones<double>(1,i);
                    break;
                }
            }
            //中间无效点处理
            double tmp = 0;
            for (size_t i = 0; i < dataLen; i++)
            {
                if (recNc[i]>0)
                    tmp = recNc[i];
                else
                    recNc[i] = tmp;
            }

            //曲线扩展
            auto curveExpand = nc::hstack({nc::ones<double>(1,halfSmoothLen)*recNc.front(), recNc, nc::ones<double>(1,halfSmoothLen)*recNc.back()});

            //曲线平滑
            auto curve = nc::zeros_like<double>(recNc);
            for (size_t i = 0; i < dataLen; i++)
            {
                curve.at(i) = nc::mean(curveExpand[nc::Slice(i, i+halfSmoothLen*2+1)]).item();
            }
            return curve.toStlVector();
            
        }
    }
} // namespace basic
